"""doc 274 — EngineImmuneSystem: the membership audit, mechanized as a nightly standing gate.

The Mutation Gauntlet (doc 274) measured what the falsification engine can certify and found
two mechanical organs worth running every night: D2 (universe-membership audit — gate
recomputation + prev-close freshness + ADV-window session-completeness vs the raw day_aggs
warehouse) and D10 (composition drift). 273's registered result was killed by exactly the
class D2 catches; 274's blinded fleets then proved even the "clean" research corpus carries
a residual contamination (post-hole ADV windows bridge the Jan-Mar warehouse gap — GLXG
2026-03-24 stored ADV $1.08M vs true tape $37k). This experiment is the immune system: it
re-audits the shared research corpus (exit_labels_cross_regime.parquet) against the raw
warehouse every night and reports membership violations, so no future inference rides a
contaminated universe unnoticed. Until the day_aggs rebuild lands (chip task_4994579a), it is
EXPECTED to fire on the residual ADV-window cohort — that is the point: a standing red light,
not a silent pass.

DORMANT-C: read-only, never imported by production, never-raises by substrate contract
(metrics() is fully guarded; a warehouse-read failure degrades to a reported error, never an
exception out of the runner).
"""
from __future__ import annotations

from pathlib import Path

from src.research.event_bus import Event, default_data_root
from src.research.experiment import Experiment, register

CORPUS = "research/exit_labels_cross_regime.parquet"
DAY_AGGS = "polygon_warehouse/day_aggs/**/*.parquet"
FRESH_MAX_DAYS = 4          # prev-close freshness (doc 273 standing rule)
WINDOW_MAX_SPAN_DAYS = 40   # 20-session ADV window must not span more than this (doc 274 finding)


@register
class EngineImmuneSystem(Experiment):
    name = "engine_immune_system"
    consumes: set[str] = set()      # audits the warehouse, not the bus

    def on_event(self, e: Event) -> None:  # no bus consumption
        return

    def metrics(self) -> dict:
        try:
            import duckdb
            root = Path(default_data_root())
            corpus = (root / CORPUS).as_posix()
            aggs = (root / DAY_AGGS).as_posix()
            con = duckdb.connect()
            # distinct ticker-days the research corpus asserts are in-universe
            rows = con.execute(
                f"SELECT DISTINCT ticker, session_date FROM read_parquet('{corpus}')").df()
            n = int(len(rows))
            if n == 0:
                return {"status": "corpus-empty", "fired": False}
            # recompute the doc-235 gate + freshness + window integrity from raw day_aggs
            audit = con.execute(f"""
              WITH b AS (
                SELECT ticker, ts_et::DATE d, open, close, volume,
                  lag(close) OVER (PARTITION BY ticker ORDER BY ts_et) pc,
                  lag(ts_et::DATE) OVER (PARTITION BY ticker ORDER BY ts_et) pd,
                  avg(volume*close) OVER (PARTITION BY ticker ORDER BY ts_et
                      ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20,
                  -- span of calendar days the 20-row ADV window actually covers
                  (ts_et::DATE - lag(ts_et::DATE, 20) OVER (
                      PARTITION BY ticker ORDER BY ts_et)) win_span
                FROM read_parquet('{aggs}'))
              SELECT ticker, strftime(d, '%Y-%m-%d') session_date,
                     (pc IS NULL OR open/pc-1 < 0.08 OR open < 0.50 OR open > 20.0
                      OR adv20 IS NULL OR adv20 < 1e6) AS gate_fail,
                     (pd IS NULL OR date_diff('day', pd, d) > {FRESH_MAX_DAYS}) AS stale_close,
                     (win_span IS NULL OR win_span > {WINDOW_MAX_SPAN_DAYS}) AS window_bridged
              FROM b""").df()
            m = rows.merge(audit, on=["ticker", "session_date"], how="left")
            missing = int(m["gate_fail"].isna().sum())          # not found in warehouse at all
            gate_bad = int((m["gate_fail"] == True).sum())       # noqa: E712 (duckdb bool)
            stale = int((m["stale_close"] == True).sum())        # noqa: E712
            bridged = int((m["window_bridged"] == True).sum())   # noqa: E712
            any_bad = int(((m["gate_fail"] == True) | (m["stale_close"] == True)  # noqa: E712
                           | (m["window_bridged"] == True) | m["gate_fail"].isna()).sum())
            worst = (m[(m["window_bridged"] == True) | (m["stale_close"] == True)]  # noqa: E712
                     .session_date.value_counts().head(5).to_dict())
            return {
                "n_audited": n,
                "n_membership_violations": any_bad,
                "frac_violations": round(any_bad / n, 4),
                "gate_fail": gate_bad, "stale_prev_close": stale,
                "adv_window_bridged": bridged, "missing_in_warehouse": missing,
                "worst_days": worst,
                "fired": bool(any_bad > 0),
            }
        except Exception as exc:        # never-raises: degrade to a reported error
            return {"status": f"audit-error: {exc!r}", "fired": False}

    def promote_gate(self) -> str | None:
        return ("standing membership immune system — no promotion path; value = a nightly red "
                "light on universe contamination (the class that killed doc 273)")

    def kill_gate(self) -> str | None:
        return None
