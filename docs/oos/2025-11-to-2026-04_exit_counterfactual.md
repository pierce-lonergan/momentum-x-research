# Exit Counterfactual Analysis — 280 Broker Trades

**Generated:** by `scripts/analyze_exit_counterfactuals.py` against `data/broker_truth/closed_trades.parquet` + `mx-arena/data/historical/{ticker}/{date}.parquet` bars.

**Goal:** for each broker trade, compute what the P&L WOULD have been under alternative exit policies (T+60s, T+5min, T+15min, T+30min, EOD, T+1, T+5). Plus MFE / MAE within the actual hold window. Identifies whether the strategy's EXITS are the bug, not the ENTRIES.

---

## §1 — Sample size
- Total broker trades: 280
- With loadable bars (counterfactuals computed): 117
- Without bars (skipped): 163

## §2 — Aggregate P&L by counterfactual exit policy

| Exit policy | Σ P&L (across valid trades) | vs ACTUAL |
|---|---:|---:|
| ACTUAL | $+36,027.28 | $+0.00 |
| T+60s | $-4,977.11 | $-41,004.39 |
| T+5min | $-1,424.82 | $-37,452.10 |
| T+15min | $-2,441.97 | $-38,469.25 |
| T+30min | $-2,186.77 | $-38,214.05 |
| EOD | $-3,535.23 | $-39,562.51 |
| T+1 open | $+59,354.39 | $+23,327.11 |
| T+5 close | $+71,568.49 | $+35,541.21 |

## §3 — MFE / MAE distribution (within actual hold window)

- **Max Favorable Excursion (% from entry)**: median +2.61%, p75 +9.05%, p90 +9.09%
- **Max Adverse Excursion (% from entry)**: median -3.98%, p25 -6.91%, p10 -12.43%

- **MFE capture ratio (actual P&L / MFE $)**: median -27.3% — i.e., median trade captured -27% of its peak favorable excursion
- Capture distribution: 42 trades NEGATIVE (had MFE but exited at loss), 4 captured <25%, 12 captured 25-75%, 13 captured >75%

## §4 — Win rate per exit policy (n=trades P&L > 0)

| Exit policy | Wins | Total | Win-rate |
|---|---:|---:|---:|
| ACTUAL | 28 | 117 | 23.9% |
| T+60s | 61 | 117 | 52.1% |
| T+5min | 66 | 117 | 56.4% |
| T+15min | 62 | 117 | 53.0% |
| T+30min | 59 | 117 | 50.4% |
| EOD | 35 | 117 | 29.9% |
| T+1 open | 16 | 40 | 40.0% |
| T+5 close | 13 | 13 | 100.0% |

## §5 — Top 10 missed gains (MFE - actual_pnl, biggest gaps)

Trades where the price reached significant favorable excursion during hold but actual exit didn't capture it.

| Ticker | Date | Entry | Actual exit | MFE $ | Actual P&L | Missed |
|---|---|---:|---:|---:|---:|---:|
| RLMD | 2026-03-09 | $5.8200 | $6.6200 | $+3,660.18 | $+1,841.60 | $+1,818.58 |
| CANF | 2026-03-04 | $10.4000 | $7.0100 | $+0.00 | $-1,800.09 | $+1,800.09 |
| CANF | 2026-03-04 | $10.4000 | $7.2500 | $+0.00 | $-1,143.45 | $+1,143.45 |
| CANF | 2026-03-04 | $10.4000 | $7.2500 | $+0.00 | $-1,071.00 | $+1,071.00 |
| LIDR | 2026-04-24 | $2.4200 | $2.3600 | $+636.24 | $-173.52 | $+809.76 |
| CANF | 2026-03-04 | $10.4000 | $7.0100 | $+0.00 | $-555.96 | $+555.96 |
| BATL | 2026-02-19 | $4.9000 | $4.0300 | $+130.48 | $-405.42 | $+535.90 |
| BATL | 2026-02-19 | $4.9000 | $4.0300 | $+123.76 | $-384.54 | $+508.30 |
| RCAT | 2026-03-09 | $15.6200 | $15.1500 | $+117.54 | $-306.91 | $+424.45 |
| CANF | 2026-03-04 | $6.8400 | $6.1500 | $+0.00 | $-412.62 | $+412.62 |
