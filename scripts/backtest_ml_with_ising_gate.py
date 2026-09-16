"""Combine v2 ML model with Ising magnetization regime gate.

Per doc 100 §2: H3 short works best in MID-magnetization × MID-breadth
regimes (+3-3.3%/trade vs extremes' +0.2-1.5%).

Hypothesis: the v2 ML continuer model may also have regime-dependent
performance. Test by gating ML predictions on the daily Ising regime.

Three modes:
  - ALL: use ML predictions on every day
  - MID-MAG: only trade when 5d-rolling magnetization is in MID tercile
  - MID-MAG x MID-BREADTH: only trade when both are MID

Walk-forward replays v2 predictions stratified by Ising regime.

Outputs:
  data/polygon_warehouse/derived/ml_x_ising_summary.json
"""
from __future__ import annotations
import sys, json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    section("STEP 1 - Load WF predictions + Ising regime")
    preds_path = (DERIVED / "ml_walkforward_results.parquet").as_posix()
    ising_path = (DERIVED / "ising_daily.parquet").as_posix()
    if not Path(preds_path).exists():
        print(f"ERROR: {preds_path} missing")
        return 1
    if not Path(ising_path).exists():
        print(f"ERROR: {ising_path} missing")
        return 1

    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")

    # Use v2 per-row predictions if available, else fall back to v1
    v2_preds = (DERIVED / "ml_v2_walkforward_predictions.parquet")
    if v2_preds.exists():
        preds_path = str(v2_preds).replace("\\", "/")
        print(f"  Using V2 predictions from {v2_preds.name}")
    else:
        print(f"  Using V1 predictions from {Path(preds_path).name}")
    con.sql(f"""
        CREATE OR REPLACE TABLE preds AS
        SELECT * FROM read_parquet('{preds_path}')
    """)
    n_preds = con.sql("SELECT COUNT(*) FROM preds").fetchone()[0]
    print(f"  preds: {n_preds:,} rows")

    con.sql(f"""
        CREATE OR REPLACE TABLE ising AS
        SELECT *,
               AVG(magnetization) OVER (ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS mag_5d
        FROM read_parquet('{ising_path}')
    """)
    n_ising = con.sql("SELECT COUNT(*) FROM ising").fetchone()[0]
    print(f"  ising: {n_ising} dates")

    section("STEP 2 - Join preds with Ising; compute regime labels")
    con.sql("""
        CREATE OR REPLACE TABLE pi AS
        SELECT p.*, i.magnetization, i.mag_5d, i.n_huge_up,
               i.n_huge_up AS breadth,
               -- Tercile labels (matching doc 100 thresholds)
               CASE WHEN i.mag_5d < -0.05 THEN 'LO'
                    WHEN i.mag_5d > 0.05 THEN 'HI'
                    ELSE 'MID' END AS mag_label,
               CASE WHEN i.n_huge_up < 10 THEN 'LO'
                    WHEN i.n_huge_up > 15 THEN 'HI'
                    ELSE 'MID' END AS breadth_label
        FROM preds p JOIN ising i ON p.d0 = i.d
    """)
    n_pi = con.sql("SELECT COUNT(*) FROM pi").fetchone()[0]
    print(f"  joined: {n_pi:,} rows")

    section("STEP 3 - Stratify ML P>=0.30 returns by regime")
    print(f"  {'mag':<5} {'breadth':<7} {'n':>5}  {'avg_t5':>9}  {'win%':>6}")
    rows = con.sql("""
        SELECT mag_label, breadth_label, COUNT(*) n,
               ROUND(AVG(y_reg)*100, 2) avg_t5,
               ROUND(100.0 * SUM(CASE WHEN y_reg > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win
        FROM pi
        WHERE prob_continuer >= 0.30
        GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:<5} {r[1]:<7} {r[2]:>5,} {r[3]:>+8.2f}% {r[4]:>5.1f}%")

    section("STEP 4 - All P>=0.30 (no gate)")
    r = con.sql("""
        SELECT COUNT(*) n,
               ROUND(AVG(y_reg)*100, 2) avg,
               ROUND(100.0 * SUM(CASE WHEN y_reg > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win
        FROM pi WHERE prob_continuer >= 0.30
    """).fetchone()
    print(f"  ALL: n={r[0]} avg={r[1]:+.2f}% win={r[2]}%")

    section("STEP 5 - Single-axis gates")
    print("\n  Gate by mag tercile (5d rolling):")
    rows = con.sql("""
        SELECT mag_label, COUNT(*) n,
               ROUND(AVG(y_reg)*100, 2) avg,
               ROUND(100.0 * SUM(CASE WHEN y_reg > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win
        FROM pi WHERE prob_continuer >= 0.30
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    for r in rows:
        print(f"    {r[0]:<5}  n={r[1]:,} avg={r[2]:+.2f}% win={r[3]}%")

    print("\n  Gate by breadth tercile:")
    rows = con.sql("""
        SELECT breadth_label, COUNT(*) n,
               ROUND(AVG(y_reg)*100, 2) avg,
               ROUND(100.0 * SUM(CASE WHEN y_reg > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win
        FROM pi WHERE prob_continuer >= 0.30
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    for r in rows:
        print(f"    {r[0]:<5}  n={r[1]:,} avg={r[2]:+.2f}% win={r[3]}%")

    section("STEP 6 - Best-bucket combinations (Ising x ML)")
    print("\n  Three-way: keep only MID_MID")
    r = con.sql("""
        SELECT COUNT(*) n,
               ROUND(AVG(y_reg)*100, 2) avg,
               ROUND(100.0 * SUM(CASE WHEN y_reg > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win
        FROM pi WHERE prob_continuer >= 0.30
          AND mag_label = 'MID' AND breadth_label = 'MID'
    """).fetchone()
    print(f"    n={r[0]:,} avg={r[1]:+.2f}% win={r[2]}%")

    print("\n  Two-way: keep MID_MID OR MID_HI (avoid extreme regimes)")
    r = con.sql("""
        SELECT COUNT(*) n,
               ROUND(AVG(y_reg)*100, 2) avg,
               ROUND(100.0 * SUM(CASE WHEN y_reg > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win
        FROM pi WHERE prob_continuer >= 0.30
          AND mag_label = 'MID' AND breadth_label IN ('MID', 'HI')
    """).fetchone()
    print(f"    n={r[0]:,} avg={r[1]:+.2f}% win={r[2]}%")

    print("\n  Avoid only LO_LO (calm, low-mag = bad)")
    r = con.sql("""
        SELECT COUNT(*) n,
               ROUND(AVG(y_reg)*100, 2) avg,
               ROUND(100.0 * SUM(CASE WHEN y_reg > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win
        FROM pi WHERE prob_continuer >= 0.30
          AND NOT (mag_label = 'LO' AND breadth_label = 'LO')
    """).fetchone()
    print(f"    n={r[0]:,} avg={r[1]:+.2f}% win={r[2]}%")

    section("STEP 7 - Persist summary")
    out = DERIVED / "ml_x_ising_summary.json"
    summary = {
        "all_n": n_pi,
        "test_descriptions": "v1 ML predictions x Ising daily regime",
        "key_finding_in_log": "see step 3-7 above for regime-stratified returns",
    }
    out.write_text(json.dumps(summary, indent=2))
    print(f"  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
