# MOMENTUM-X: System Recovery, Scheduling & Audit Infrastructure

> **NOTE (2026-04-06)**: This document was last updated at D115. The system is now at **D210**. Recovery infrastructure, scheduling, and audit systems have continued to evolve — see D160–D210 decision docs.
> **Last Updated**: 2026-03-16 (D115: Tiered Kelly Criterion, D114 news catalyst broadening, D113 heartbeat fix)
> **Status**: Production-ready (paper trading) — Task Scheduler active, configuration freeze in progress
> **Test Coverage**: 352+ dedicated tests across recovery, state persistence, scheduling, experimentation, exit strategies, routing, Kelly tier, and audit modules (+43 D115, +131 D109+D110, +30 D112)

---

## Table of Contents

1. [System Recovery](#1-system-recovery)
2. [Session State Persistence](#2-session-state-persistence)
3. [Stop-Loss Resubmission](#3-stop-loss-resubmission)
4. [Circuit Breakers](#4-circuit-breakers)
5. [Scheduling Infrastructure](#5-scheduling-infrastructure)
6. [Audit & Observability](#6-audit--observability)
7. [Trade Journal](#7-trade-journal)
8. [Experiment Journal (D102)](#8-experiment-journal-d102)
9. [Session Reports](#9-session-reports)
10. [Live Dashboard](#10-live-dashboard)
11. [Console Observability (D102)](#11-console-observability-d102)
12. [Test Coverage Matrix](#12-test-coverage-matrix)
13. [Failure Scenarios & Responses](#13-failure-scenarios--responses)
14. [File Reference](#14-file-reference)

---

## 1. System Recovery

### 1.1 Recovery Architecture

The system implements a two-tier recovery strategy that can survive mid-session crashes without losing position state, stop-out cooldowns, or phase completion flags.

```
┌──────────────────────────────────────────────────────┐
│                  PROCESS RESTART                      │
│                                                       │
│  ┌─────────────────────┐   ┌───────────────────────┐ │
│  │ session_state.json  │   │  Alpaca REST API       │ │
│  │ (D64 Enhanced)      │   │  GET /v2/positions     │ │
│  │                     │   │  GET /v2/orders        │ │
│  │ • targets           │   │                        │ │
│  │ • tranches_filled   │   │  • qty (authoritative) │ │
│  │ • signal_price      │   │  • avg_entry_price     │ │
│  │ • stop_order_id     │   │  • current_price       │ │
│  │ • tranche_order_ids │   │  • side                │ │
│  │ • realized_pnl      │   │                        │ │
│  │ • stopped_out_tickers│  │                        │ │
│  │ • eod_close_completed│  │                        │ │
│  │ • phase0_completed  │   │                        │ │
│  └──────────┬──────────┘   └──────────┬────────────┘ │
│             │                         │               │
│             └──────────┬──────────────┘               │
│                        ▼                              │
│         ┌──────────────────────────┐                  │
│         │ sync_from_state_and_     │                  │
│         │ orders() — MERGE         │                  │
│         │                          │                  │
│         │ State file enriches,     │                  │
│         │ Alpaca is authoritative  │                  │
│         │ for position existence   │                  │
│         └──────────────────────────┘                  │
│                        │                              │
│              Falls back to D56                        │
│              if no state file                         │
└──────────────────────────────────────────────────────┘
```

### 1.2 D64 Enhanced Recovery (Primary Path)

When `session_state.json` exists and is from today's date, the system performs a full merge:

1. **Load session state** from `data/session_state.json`
2. **Fetch live positions** from Alpaca `GET /v2/positions`
3. **Fetch open orders** from Alpaca `GET /v2/orders?status=open`
4. **Merge**: For each Alpaca position, check if session state has enrichment data (targets, tranches, signal_price, stop_order_id). If yes, use the richer state data. If no, fall back to D56 estimation.
5. **Restore TrancheExitMonitor** from state + active orders
6. **Restore StopResubmitter** from state stop_order_ids
7. **Restore phase flags**: `phase0_completed`, `eod_close_completed`
8. **Restore cooldowns**: `stopped_out_tickers` set
9. **Restore daily P&L** for circuit breaker continuity

**Key Invariant**: Alpaca is authoritative for position *existence* (qty, side). The state file only *enriches* with metadata that Alpaca doesn't track.

### 1.3 Post-Merge Validation (D108)

After `sync_from_state_and_orders()` completes, `_validate_recovered_positions()` performs sanity checks on all recovered positions:

| Check | Action |
|---|---|
| `remaining_qty < 0` | Clamp to 0, log WARNING |
| `remaining_qty > qty` | Clamp to qty, log WARNING |
| `tranches_filled < 0` or `> 3` | Clamp to [0, 3], log WARNING |
| `stop_loss >= entry_price` | Log WARNING (may be trailing — don't auto-correct) |
| `len(target_prices) != 3` | Log WARNING (don't crash) |

This catches impossible state from corrupt data or edge cases without crashing the recovery path.

### 1.4 Post-Recovery Connectivity Probe (D108)

After state load and before orphan reconciliation, `main.py` verifies the broker link is still alive:

1. Call `await client.get_account()`
2. If fails: log CRITICAL, retry once after 5s
3. If retry fails: log CRITICAL, continue anyway (better to attempt trading than abort)

This catches transient connectivity issues between the initial API key validation and the trading loop.

### 1.5 D56 Basic Recovery (Fallback Path)

When no state file exists (first run of day, or state file corrupt/stale):

1. **Fetch positions** from Alpaca
2. **Estimate stops** from config: `entry × (1 - stop_loss_pct)`
3. **Estimate targets** from config: `entry × (1 + 5%/10%/20%)`
4. **No tranche/cooldown/flag recovery** (fresh start)

This path ensures the system never crashes on startup even if the state file is missing.

### 1.4 Recovery Data Flow

| Data Element | Source (D64) | Source (D56) | Survives Restart? |
|---|---|---|---|
| Position qty | Alpaca API | Alpaca API | Always |
| Entry price | State file | Alpaca `avg_entry_price` | Always |
| Target prices | State file | Estimated from config | D64 only |
| Tranches filled | State file | 0 (unknown) | D64 only |
| Stop order ID | State file | None | D64 only |
| Signal price | State file | 0.0 | D64 only |
| Stopped-out tickers | State file | Empty set | D64 only |
| EOD close flag | State file | False | D64 only |
| Phase 0 flag | State file | False | D64 only |
| Daily realized P&L | State file | 0.0 | D64 only |

---

## 2. Session State Persistence

### 2.1 Architecture

**File**: `src/execution/session_state.py`
**Storage**: `data/session_state.json`
**Format**: JSON with atomic writes

The `SessionStateManager` provides crash-safe persistence for execution state that the Alpaca API does not track.

### 2.2 Data Model

```
SessionState
├── version: int (always 1)
├── session_date: str (ISO date, e.g. "2026-03-11")
├── last_update: str (UTC ISO-8601 timestamp)
├── daily_realized_pnl: float
├── stopped_out_tickers: list[str]
├── eod_close_completed: bool
├── phase0_completed: bool
└── positions: dict[str, PositionState]
    └── PositionState
        ├── ticker, qty, entry_price, signal_price
        ├── stop_loss, target_prices: list[float]
        ├── tranches_filled, remaining_qty
        ├── realized_pnl
        ├── entry_order_id, stop_order_id
        ├── tranche_order_ids: list[str]
        ├── opened_at
        ├── trailing_stop_active: bool
        └── peak_price: float
```

### 2.3 Atomic Writes

All saves use a two-step atomic write to prevent corruption if the process crashes mid-write:

1. Write to `session_state.tmp`
2. `os.replace(tmp, session_state.json)` — atomic on all OS

This guarantees the state file is always either the old complete version or the new complete version, never a partial write.

### 2.4 Backup Rotation (D108)

Before each atomic write, the current state file is copied to `session_state.json.bak` via `shutil.copy2`. This provides a single-generation rollback if the primary file is corrupted.

**Fallback load order**:
1. Load `session_state.json` (primary)
2. If corrupt → load `session_state.json.bak` (backup)
3. If both corrupt → return `None` (fresh start)

### 2.5 State Diff Logging (D108)

Each `save()` compares the new state dict against `_last_saved_dict` and logs field-level changes at DEBUG:

- Top-level fields: `"State change: daily_realized_pnl: 0.0 -> 127.50"`
- Position-level: `"State change [AAPL]: remaining_qty: 100 -> 67"`
- Excludes `last_update` to reduce noise

This provides a complete audit trail of state evolution for debugging without additional I/O.

### 2.6 Staleness Protection

On load, the session date is compared to today:

- **Today's date** → state accepted, positions and flags restored
- **Yesterday's date** → state rejected (returns `None`), fresh start
- **Corrupt JSON** → try backup (D108), else fresh start
- **Missing file** → state rejected, fresh start

This prevents carrying overnight state into a new trading session.

### 2.7 State Update Points

The state file is saved after every significant state change:

| Event | Fields Updated |
|---|---|
| Order fill | Position added (qty, entry, targets, stop, order_ids) |
| Tranche fill | `tranches_filled++`, `remaining_qty`, `stop_loss` (ratcheted) |
| Stop-out | `stopped_out_tickers` += ticker, position removed |
| Trailing stop activated | `trailing_stop_active = True`, `peak_price` |
| EOD close | `eod_close_completed = True`, positions removed |
| Phase 0 complete | `phase0_completed = True` |
| P&L change | `daily_realized_pnl` updated |

---

## 3. Stop-Loss Resubmission

### 3.1 Architecture

**File**: `src/execution/stop_resubmitter.py`
**Purpose**: When tranche exits fill and the stop ratchets, the old bracket stop at Alpaca becomes stale. The StopResubmitter replaces it.

### 3.2 Ratchet Schedule

| Event | Stop Moves To | Rationale |
|---|---|---|
| T1 fills (33% exit) | Breakeven (entry price) | Lock in cost basis |
| T2 fills (33% exit) | T1 target price | Lock in partial profit |
| T3 fills (final 34%) | N/A (position closed) | Full exit |

### 3.3 Two-Phase Resubmission

The resubmission process is deliberately two-phase to maintain safety:

```
Phase 1: Cancel old stop
    ├── Success → proceed to Phase 2
    └── Failure → old stop remains active (SAFE)
                   Return error, do NOT submit new stop

Phase 2: Submit new stop at ratcheted price
    ├── Success → update TrackedStop, return success
    └── Failure → CRITICAL: old stop canceled, new stop NOT placed
                   Log CRITICAL, flag for manual intervention
```

### 3.4 Phase 2 Retry Loop (D108)

Phase 2 (new stop submission) was the most dangerous failure mode — a single failure left the position unprotected with only a CRITICAL log. D108 wraps Phase 2 in a retry loop:

- **3 attempts** with exponential backoff (1s, 2s, 4s)
- On non-final failure: log WARNING, sleep, retry
- On final failure: log CRITICAL with attempt count, return failure result
- On success: break immediately

This reduces the probability of an unprotected position from P(single_failure) to P(failure)^3.

### 3.5 Safety Invariants

1. **Ratchet-up only**: `new_stop_price >= old_stop_price` is enforced. The stop never moves down.
2. **Cancel before submit**: The old stop must be successfully canceled before the new one is submitted.
3. **Retry on Phase 2 failure (D108)**: 3 attempts with exponential backoff before declaring CRITICAL.
4. **Recovery on restart**: Stop order IDs are persisted in session state and restored via `StopResubmitter.register_stop()`.

### 3.5 D100 Hybrid Stop Conversion

With D100, the initial stop management changed from OTO brackets to a hybrid approach:

```
BEFORE D100 (OTO bracket):
  Buy order → OTO bracket auto-creates stop leg
  Tranche sells → 403 CONFLICT (can't sell while OTO stop active)

AFTER D100 (hybrid):
  1. Buy fills → cancel OTO stop leg
  2. Submit standalone stop for full qty
  3. Submit tranche limit sells (no conflict)
  4. Register standalone stop for ratcheting
```

This eliminates the 403 error that prevented all tranche profit-taking.

---

## 4. Circuit Breakers

### 4.1 Architecture

**File**: `src/utils/circuit_breaker.py`
**Pattern**: Async-compatible circuit breaker (pybreaker is sync-only)

### 4.2 State Machine

```
       ┌──────────┐
       │  CLOSED   │◄──── success in HALF_OPEN
       │ (normal)  │
       └────┬──────┘
            │ fail_max consecutive failures
            ▼
       ┌──────────┐
       │   OPEN    │──── rejects ALL calls
       │ (tripped) │     for reset_timeout seconds
       └────┬──────┘
            │ reset_timeout expires
            ▼
       ┌──────────┐
       │ HALF_OPEN │──── allows ONE test call
       │  (probe)  │
       └──────────┘
            │
     success → CLOSED
     failure → OPEN
```

### 4.3 Per-Service Configuration

| Service | Breaker Name | Fail Max | Reset Timeout | Rationale |
|---|---|---|---|---|
| Alpaca REST API | `alpaca_rest` | 3 failures | 30s | Broker downtime is usually brief |
| LLM Provider | `llm_provider` | 5 failures | 60s | 3 candidates × 4 agents = 12 concurrent calls; 2 was too hair-trigger |
| News/Data APIs | `news_api` | 5 failures | 60s | Non-critical, can trade without news |

### 4.4 Usage Pattern

```python
# Context manager (automatic success/failure recording)
async with alpaca_breaker:
    result = await client.get_positions()

# Manual (for custom error handling)
alpaca_breaker.check()  # Raises CircuitBreakerError if OPEN
try:
    result = await do_work()
    alpaca_breaker.record_success()
except Exception:
    alpaca_breaker.record_failure()
    raise
```

### 4.5 Exponential Backoff on Reset Timeout (D108)

When a HALF_OPEN probe fails and the breaker re-trips to OPEN, the reset timeout doubles:

```
Trip 1: 30s → Trip 2: 60s → Trip 3: 120s → Trip 4: 240s → Trip 5+: 300s (cap)
```

- **Cap**: `MAX_RESET_TIMEOUT = 300s` (5 minutes) prevents runaway backoff
- **Recovery reset**: On successful HALF_OPEN probe, timeout returns to `_base_reset_timeout`
- **Backward compat**: `reset_timeout` property returns `_current_reset_timeout`

This prevents aggressive probing during extended outages while still recovering quickly from transient failures.

### 4.6 Aggregate System Health Gate (D108)

`check_system_health()` provides a single boolean check for trading readiness:

- Returns `(healthy: bool, problems: list[str])`
- Currently checks: Alpaca REST breaker state (only critical breaker)
- LLM/news breakers being OPEN don't halt trading (graceful degradation already handled)

**main.py integration**: Before Phase 2 evaluation, `check_system_health()` is called. If unhealthy, the cycle is skipped with a 30s sleep and WARNING log. This prevents wasted evaluations when the broker is unreachable.

### 4.7 Dashboard Integration

The `get_breaker_status()` function returns the current state of all breakers for the live dashboard display.

---

## 5. Scheduling Infrastructure

### 5.1 Overview

Momentum-X uses a multi-layer scheduling infrastructure for fully automated daily paper trading sessions. D101+ added cross-platform Python launcher, NYSE holiday calendar, heartbeat watchdog, and HTTP health/control server.

| Component | Purpose | Added |
|---|---|---|
| `scripts/install_scheduler.ps1` | One-time Windows Task Scheduler setup | D90 |
| `scripts/daily_paper_trade.ps1` | Daily PowerShell launcher with safety checks | D90 |
| `scripts/run_trading.py` | **Cross-platform Python launcher** | **D101** |
| `src/scheduling/market_calendar.py` | **NYSE holiday calendar (2025-2027)** | **D101** |
| `src/scheduling/heartbeat.py` | **Internal heartbeat watchdog** | **D101** |
| `src/scheduling/health_server.py` | **HTTP health/control server (port 9091)** | **D101** |
| `scripts/run_phase3.ps1` | Manual command runner with secrets | D90 |

### 5.2 Windows Task Scheduler Configuration

**Task Name**: `MomentumX-PaperTrading`

| Setting | Value | Rationale |
|---|---|---|
| Trigger | Daily at 3:30 AM local | Before Phase 0 pre-market research |
| Wake from sleep | Yes | PC may be sleeping at 3:30 AM |
| Start if on battery | Yes | Laptops should still run |
| Don't stop on battery | Yes | Don't kill mid-session |
| Restart on failure | 3 retries, 5 min apart | Self-healing on transient errors |
| Execution time limit | 14 hours | Kills at 5:30 PM if stuck |
| Multiple instances | Ignore new | Prevents duplicate sessions |
| Run level | Highest | Required for process management |

**Install**: `powershell -ExecutionPolicy Bypass -File scripts\install_scheduler.ps1` (requires Admin)
**Uninstall**: `scripts\install_scheduler.ps1 -Uninstall`
**Verify**: `Get-ScheduledTask -TaskName 'MomentumX-PaperTrading'`

### 5.3 Daily Launcher Safety Features (D90)

The `daily_paper_trade.ps1` launcher implements multiple safety layers before starting the 13-hour trading session:

#### 5.3.1 Weekend Skip
```
Check DayOfWeek → Saturday or Sunday → exit 0 (skip)
```

#### 5.3.2 Stale Process Cleanup
Searches for Python processes matching `main paper` or `momentum` and kills them. Prevents zombie processes from previous crashed sessions that could:
- Corrupt shared state files
- Create ghost positions
- Hold ports/locks

#### 5.3.3 Lock File Management
- Writes PID to `logs/momentum-x.lock` on start
- On startup, checks if lock file exists with a live process → exits (another instance running)
- If lock file exists with dead PID → removes stale lock
- Lock file cleaned up on exit (including error paths)

#### 5.3.4 Pre-flight Validation
Runs a Python import check that validates:
- Python interpreter is available
- `config.settings` module loads
- `litellm`, `httpx`, `dotenv` packages import
- `ALPACA_API_KEY` is set in `.env`
- `TOGETHER_AI_API_KEY` is set in `.env`

If any check fails, the session never starts and a clear error message is logged.

#### 5.3.5 Stale Session State Cleanup
Checks `data/session_state.json` for yesterday's date and removes it, ensuring a clean D64 start rather than carrying stale state.

#### 5.3.6 Toast Notifications (D97/D98)
- **Start notification**: "Momentum-X Starting — Paper trading session {date}"
- **Failure notification**: "Momentum-X FAILED — Exit code {N} — check {logfile}"
- Wrapped in `SilentlyContinue` to prevent WinRT type-loading errors from crashing the script under Task Scheduler

#### 5.3.7 Transcript Capture (D98)
`Start-Transcript` captures ALL PowerShell output to `logs/transcript_YYYY-MM-DD.log` before `ErrorActionPreference` is set. This ensures crash diagnostics are preserved even when Task Scheduler failures leave zero diagnostics.

### 5.4 Trading Phase Timing

The scheduler starts at 3:30 AM ET. The Python process manages all phase transitions internally:

```
 4:00 AM ─── Phase 0: Pre-market research (news, SEC, technicals)
 4:00 AM ─── Phase 1: Pre-market scanning (overlaps with Phase 0)
 9:25 AM ─── Phase 1.5: Fast-path 3-agent scoring (5 min before open)
 9:30 AM ─── Phase 2: Market open, fast-path fire + parallel eval
              + D102: Experiment replay (22 variants per candidate, ~2ms)
              + D102: Verdict summary log (BUY/HOLD/NO_TRADE counts)
10:00 AM ─── Phase 3: Position management + exit intelligence
              + D102: Five-minute heartbeat (position status every 5 cycles)
 3:55 PM ─── Force-close all positions (D76)
 4:00 PM ─── Phase 4: Session report (incl. experiment summary), Shapley, shutdown
```

**Actual scheduled task**: `MomentumX-PaperTrading` triggers at **4:30 AM ET** daily (wakes from sleep). Phase 0 begins immediately when `hour_et >= 4`.

### 5.5 Secrets Management

API keys are stored OUTSIDE the git repository at `$HOME\momentum-x-secrets.env`. The launcher copies this file to the project's `.env` (gitignored) at startup. This pattern ensures:
- Secrets never appear in git history
- Multiple machines can have different keys
- The `.env.example` template guides new setups

### 5.6 Logging

| Log File | Contents | Retention |
|---|---|---|
| `logs/paper_YYYY-MM-DD.log` | Full Python stdout/stderr | Per-day |
| `logs/transcript_YYYY-MM-DD.log` | PowerShell transcript (D98) | Per-day |
| `data/journals/journal_YYYY-MM-DD_HHMMSS.jsonl` | Trade journal entries | Per-session |
| `data/session_reports/session_YYYY-MM-DD_*.json` | EOD session reports | Per-session |

### 5.7 NYSE Holiday Calendar (D101+)

**File**: `src/scheduling/market_calendar.py`
**Purpose**: Pre-flight validation to skip trading on market holidays, with early close awareness.

**Static holiday data** for 2025-2027 (all observed dates, weekday-adjusted):

| Holiday | 2025 | 2026 | 2027 |
|---|---|---|---|
| New Year's Day | Jan 1 | Jan 1 | Jan 1 |
| MLK Day | Jan 20 | Jan 19 | Jan 18 |
| Presidents' Day | Feb 17 | Feb 16 | Feb 15 |
| Good Friday | Apr 18 | Apr 3 | Mar 26 |
| Memorial Day | May 26 | May 25 | May 31 |
| Juneteenth | Jun 19 | Jun 19 | Jun 18 (Fri) |
| Independence Day | Jul 4 | Jul 3 (obs) | Jul 5 (obs) |
| Labor Day | Sep 1 | Sep 7 | Sep 6 |
| Thanksgiving | Nov 27 | Nov 26 | Nov 25 |
| Christmas | Dec 25 | Dec 25 | Dec 24 (obs) |

**Early close days** (1:00 PM ET): Day after Thanksgiving, Christmas Eve (when weekday), July 3 (when weekday).

**Key functions**:
- `is_market_holiday(date) → bool` — checks against static holiday list
- `is_early_close(date) → bool` — checks early close days
- `is_trading_day(date) → bool` — combines weekend + holiday check
- `check_market_open(date) → (should_run: bool, reason: str)` — pre-flight validation
- `get_holiday_name(date) → str | None` — returns holiday name for logging

**Usage** (in `scripts/run_trading.py`):
```python
should_run, reason = check_market_open(date.today())
if not should_run:
    logger.info(f"Market closed: {reason}")
    sys.exit(0)
```

**Test coverage**: 37 tests in `tests/unit/test_market_calendar.py` covering all holidays 2025-2027, weekends, early close days, and a parametrized test verifying all holidays fall on weekdays.

### 5.8 Heartbeat Watchdog (D101+)

**File**: `src/scheduling/heartbeat.py`
**Purpose**: Internal self-monitoring that detects hung/frozen trading processes.

**Architecture**:
```
Trading Loop ──pulse()──→ HeartbeatWatchdog ──monitor()──→ Timeout?
     │                         │                              │
     │                    Last pulse time               ┌─────┴─────┐
     │                    Phase tracking                │  75%: WARN │
     │                    Pulse count                   │ 100%: KILL │
     │                                                  └───────────┘
```

**Adaptive timeout by market phase**:

| Phase | Timeout | Rationale |
|---|---|---|
| Market hours (9:30-4:00 ET) | 30 minutes | Tight monitoring during active trading |
| Pre-market (4:00-9:30 ET) | 60 minutes | Longer gaps between scan cycles |
| Outside hours | 120 minutes | Process may be idle waiting for market |

**Key methods**:
- `pulse(phase="")` — called by trading loop each iteration to signal liveness
- `status` → dict with `alive`, `seconds_since_pulse`, `timeout_seconds`, `phase`, `pulse_count`
- `start()` — launches daemon monitor thread
- `stop()` — stops monitor thread

**Warning/shutdown behavior**:
- At 75% of timeout: logs WARNING with seconds remaining
- At 100% of timeout: triggers `shutdown_callback` (configurable) or sends SIGTERM/CTRL_BREAK_EVENT

**Test coverage**: 10 tests in `tests/unit/test_heartbeat.py` covering initial state, pulse reset, phase tracking, pulse count, start/stop lifecycle, and timeout callback.

### 5.9 Health/Control HTTP Server (D101+)

**File**: `src/scheduling/health_server.py`
**Purpose**: HTTP control plane for external monitoring, pausing, and graceful shutdown.

**Port**: 9091 (daemon thread, non-blocking)

**Endpoints**:

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/health` | GET | No | Basic health check + heartbeat status |
| `/status` | GET | Yes | Full state: positions, P&L, phase, uptime |
| `/pause` | POST | Yes | Pause new entries (existing positions managed) |
| `/resume` | POST | Yes | Resume trading |
| `/shutdown` | POST | Yes | Request graceful shutdown |

**Authentication**: Optional `X-Auth-Token` header validated against `MOMENTUM_HEALTH_TOKEN` environment variable. If env var not set, auth is disabled (development mode).

**Thread-safe state**: `TradingControlState` class provides:
- `is_paused` — checked by main loop before new entries
- `shutdown_requested` — checked by main loop each iteration
- `update_status(positions, pnl, phase)` — called by trading loop
- `get_status()` → dict with full state including uptime

**Usage** (integration with main trading loop):
```python
control = TradingControlState()
server = HealthControlServer(control_state=control, port=9091)
server.start()

# Main loop checks:
if control.shutdown_requested:
    break
if control.is_paused:
    continue  # Skip new entries
```

**Test coverage**: 12 tests in `tests/unit/test_health_server.py`:
- 5 unit tests for `TradingControlState` (initial state, pause/resume, shutdown, status, uptime)
- 7 HTTP integration tests on port 19091 (health, status, pause, resume, shutdown, auth)

### 5.10 Cross-Platform Python Launcher (D101+)

**File**: `scripts/run_trading.py`
**Purpose**: Python replacement for Windows-only `daily_paper_trade.ps1`. Works on Windows, Linux, and macOS.

**Features**:
- NYSE holiday calendar integration via `check_market_open()`
- Lock file management with stale process cleanup
- Pre-flight validation (Python imports, API keys, environment)
- `--force` flag to skip holiday/weekend checks
- `--check-only` flag for pre-flight validation without starting
- `--secrets-file` option for custom secrets location
- Runs `python -m main paper` as subprocess with streaming output

**Safety checks** (same as PS1 launcher + holiday calendar):
1. Weekend/holiday check via `market_calendar.check_market_open()`
2. Stale process cleanup (finds and reports matching Python processes)
3. Lock file management (PID-based, stale lock detection)
4. Environment validation (API keys, Python imports)
5. Session state staleness check

---

## 6. Audit & Observability

### 6.1 Metrics Registry

**File**: `src/monitoring/metrics.py`
**Pattern**: Global singleton `MetricsRegistry` with zero external dependencies

The metrics system provides O(1) metric updates that never block the trading pipeline. All metrics are in-memory with two export formats.

### 6.2 Metric Categories

#### Pipeline Metrics
| Metric | Type | Description |
|---|---|---|
| `mx_scan_iterations_total` | Counter | Total scan iterations |
| `mx_scan_candidates_found_total` | Counter | Candidates passing EMC filter |
| `mx_pipeline_latency_seconds` | Histogram | Per-candidate evaluation latency |
| `mx_evaluations_total` | Counter | Total candidates evaluated |

#### Agent Metrics
| Metric | Type | Description |
|---|---|---|
| `mx_agent_latency_seconds` | Histogram | Per-agent LLM call latency |
| `mx_agent_errors_total` | Counter | Agent timeouts + parse failures |
| `mx_agent_elo_rating{agent}` | Gauge | Per-agent Elo rating |
| `mx_agent_data_fill_rate{agent}` | Gauge | Per-agent data completeness |

#### Execution Metrics
| Metric | Type | Description |
|---|---|---|
| `mx_orders_submitted_total` | Counter | Orders sent to Alpaca |
| `mx_orders_filled_total` | Counter | Orders confirmed filled |
| `mx_orders_rejected_total` | Counter | Orders rejected by broker |
| `mx_orders_partial_fills_total` | Counter | Partial fill events |
| `mx_open_positions` | Gauge | Current open position count |
| `mx_session_trades_total` | Counter | Completed trades this session |
| `mx_fill_slippage_bps` | Histogram | Fill slippage distribution |

#### Risk Metrics
| Metric | Type | Description |
|---|---|---|
| `mx_circuit_breaker_activations_total` | Counter | Circuit breaker trips |
| `mx_daily_pnl_dollars` | Gauge | Current daily realized P&L |
| `mx_risk_vetoes_total` | Counter | Trades vetoed by Risk Agent |

#### Phase Timing Metrics (D96)
| Metric | Type | Description |
|---|---|---|
| `mx_phase0_duration_seconds` | Gauge | Phase 0 wall-clock duration |
| `mx_phase2_duration_seconds` | Gauge | Phase 2 wall-clock duration |
| `mx_phase3_cycles_total` | Counter | Phase 3 monitoring cycles |

#### Exit Event Metrics (D96)
| Metric | Type | Description |
|---|---|---|
| `mx_stop_outs_total` | Counter | Stop-loss exits |
| `mx_smart_exits_total` | Counter | D78 smart exit events |

#### Data Completeness Metrics
| Metric | Type | Description |
|---|---|---|
| `mx_data_complete_agents_total` | Counter | Agent evaluations with full data |
| `mx_data_partial_agents_total` | Counter | Agent evaluations with partial data |
| `mx_data_empty_agents_total` | Counter | Agent evaluations with no data (confabulation risk) |

### 6.3 Export Formats

#### JSON Snapshot
```python
metrics = get_metrics()
snap = metrics.snapshot()
# Returns nested dict: pipeline, agents, risk, gex, execution,
#   debate, phase_timing, exit_events, agent_signals, data_completeness
```

#### Prometheus Text Format
```python
prom_text = metrics.to_prometheus()
# Standard Prometheus exposition format with HELP/TYPE annotations
# Compatible with Prometheus scraping or Pushgateway
```

### 6.4 Disk Snapshots (D108)

`MetricsRegistry.save_snapshot(output_dir)` persists the JSON snapshot to disk for crash-recovery observability:

- **Location**: `data/metrics/metrics_{ISO_timestamp}.json`
- **Frequency**: Every 5th Phase 3 cycle (~5 minutes)
- **Rotation**: Keeps last 50 files, deletes oldest
- **Non-fatal**: All exceptions caught at DEBUG level

This provides post-crash visibility into the metrics state at the time of failure, without requiring the in-memory MetricsRegistry to survive the crash.

### 6.5 Per-Agent Signal Distribution (D96)

The metrics registry tracks every agent's signal direction:

```python
metrics.record_agent_signal("news_agent", "STRONG_BULL")
metrics.record_agent_signal("tech_agent", "BULL")

# At EOD:
summary = metrics.get_agent_signal_summary()
# {"news_agent": {"STRONG_BULL": 5, "BULL": 3}, ...}
```

This reveals systematic biases (e.g., an agent that always signals BULL regardless of input).

---

## 7. Trade Journal

### 7.1 Architecture

**File**: `src/analysis/trade_journal.py`
**Storage**: `data/journals/journal_{DATE}_{TIMESTAMP}.jsonl`
**Format**: JSONL (one JSON object per line) for streaming writes

The trade journal captures the COMPLETE data context for every candidate evaluation. All data that was previously ephemeral (logged to stdout, then lost) is now persisted.

### 7.2 Data Captured Per Evaluation

```
JournalEntry
├── Identity: trade_id, ticker, timestamp, session_date, phase
├── Candidate Data: price, gap_pct, rvol, float_shares, market_cap
├── Input Data Provenance
│   ├── news_items_count, headlines, sources
│   ├── technical_indicators
│   ├── price_bars_count
│   ├── sec_filings_count, filing_types
│   └── options_data, GEX data
├── Agent Signals (per agent)
│   ├── signal, confidence, reasoning (capped 500 chars)
│   ├── key_data, sources_used
│   ├── model_id, prompt_variant_id, latency_ms
│   ├── Risk-specific: verdict, score, veto_reason
│   ├── News-specific: catalyst_type, specificity, sentiment
│   └── Tech-specific: pattern, breakout_confirmed
├── Data Quality (per agent): status, missing_fields
├── MFCS Scoring: mfcs, component_scores, risk_score, thresholds
├── Debate Result: verdict, bull/bear strength, divergence
├── Final Verdict: action, confidence, entry, stop, targets, sizing
├── Pipeline Metrics: latency_ms, arena_variant_map
├── Execution (D66)
│   ├── order_id, fill_price, fill_qty, slippage_bps
│   ├── order_submitted_at, order_status, rejection_code
│   ├── time_to_fill_s, stop_order_id, partial_fill_qty
│   └── (recorded after order fill)
└── Position Outcome (at close)
    ├── exit_price, exit_time, realized_pnl
    ├── hold_duration_minutes, tranches_filled
    ├── trailing_stop_activated
    ├── exit_reason (D78): TRANCHE_FILL, TRAILING_STOP, etc.
    └── exit_signal_scores
```

### 7.3 Recording Lifecycle

```
evaluate_candidate()
    │
    ├── journal.create_entry(trade_id, candidate)
    ├── journal.record_input_data(entry, news, market_data, sec, options)
    ├── journal.record_agent_signals(entry, signals, variant_map)
    ├── journal.record_mfcs(entry, scored, settings)
    ├── journal.record_debate(entry, debate_result)
    ├── journal.record_verdict(entry, verdict)
    └── journal.flush(entry)  ←── writes to disk (crash-safe)

execute_verdict()
    └── journal.record_fill(trade_id, order_id, fill_price, ...)

close_position()
    └── journal.record_close(trade_id, exit_price, pnl, exit_reason, ...)
```

### 7.4 Critical Invariants

1. **Append-only** during a session (no edits to past entries)
2. **Flushed at every evaluation** — no data loss on crash
3. **All timestamps UTC ISO-8601**
4. **JSONL format** for efficient streaming reads
5. **One file per session** for clean separation

### 7.5 Post-Session Analysis

```python
# Load a single session
entries = TradeJournal.load(Path("data/journals/journal_2026-03-11_093000.jsonl"))

# Load all sessions
all_entries = TradeJournal.load_all_sessions()

# Session summary
journal = TradeJournal(session_date="2026-03-11")
summary = journal.session_summary()
# Returns: total_evaluations, buy_count, no_trade_count,
#   closed_count, total_pnl, avg_mfcs, avg_pipeline_latency_ms,
#   unique_tickers, agent_data_quality
```

---

## 8. Experiment Journal (D102)

### 8.1 Architecture

**File**: `src/experiments/journal.py`
**Storage**: `data/experiments/experiment_journal_{DATE}.jsonl`
**Format**: JSONL (one JSON object per line) — append-only, crash-safe

The Experiment Journal captures parameter replay results for every candidate evaluation. After the primary MFCS score is computed, the orchestrator replays the same agent signals through 22 parameter variants and records how each would have scored, whether it would have entered, and what position size/stop it would have used.

### 8.2 Data Captured Per Evaluation

```
ExperimentJournalEntry
├── trade_id: str (matches TradeJournal trade_id)
├── ticker: str
├── timestamp: str (UTC ISO-8601)
├── primary_mfcs: float (original score)
├── primary_action: str (BUY/HOLD/NO_TRADE)
├── primary_stop_loss: float | None
└── variant_results: list[VariantResult]
    └── VariantResult
        ├── experiment_id: str (e.g. "atr_sweep")
        ├── variant_id: str (e.g. "atr_1.5")
        ├── overrides: dict (e.g. {"execution.initial_stop_atr_multiplier": 1.5})
        ├── mfcs: float (re-scored MFCS)
        ├── would_enter: bool (MFCS >= variant threshold?)
        ├── stop_loss: float | None (variant stop price)
        ├── position_size_qty: int | None
        ├── position_size_pct: float | None
        └── reasoning: str (e.g. "MFCS=0.412 vs threshold=0.25")
```

### 8.3 Active Experiments (5 experiments, 22 variants)

| Experiment | Type | Variants | What It Tests |
|---|---|---|---|
| `atr_sweep` | parameter | 4 (1.5x, 2.0x, 2.5x, 3.0x) | Optimal stop distance for small-cap momentum |
| `threshold_sweep` | threshold | 5 (0.15-0.35) | Optimal MFCS buy threshold |
| `weight_variants` | weight | 3 (news_heavy, tech_heavy, balanced) | Optimal agent weight allocation |
| `lambda_sweep` | lambda | 5 (0.05-0.30) | Optimal risk aversion penalty |
| `deflation_sweep` | parameter | 5 (0.4-0.8) | Optimal LLM confidence deflation |

### 8.4 Experiment Registry

**File**: `src/experiments/registry.py`

The `ExperimentRegistry` loads experiment definitions from `data/experiments/experiments.yaml` and provides:

- `load(yaml_path)` — Parse YAML, validate experiment structure
- `get_active_experiments()` — Return only enabled experiments
- `apply_overrides(settings, overrides)` — **Deep-copy** Settings with variant overrides applied (never mutates original)
- `validate_overrides(settings, overrides)` — Validate override keys against Settings schema

**`apply_overrides` safety guarantees**:
- Uses Pydantic v2 `model_copy(update=...)` — immutable copy-on-write
- Dotted-path keys (e.g., `"scoring.mfcs_buy_threshold"`) map to nested sub-configs
- Unknown sections/fields are skipped with warnings (never crash)
- Empty overrides return the original Settings object (no copy overhead)

### 8.5 Configuration

**File**: `config/settings.py` — `ExperimentSettings` sub-config

| Setting | Value | Description |
|---|---|---|
| `experiments.enabled` | `True` | Master switch for experiment replay |
| `experiments.yaml_path` | `data/experiments/experiments.yaml` | Path to experiment definitions |
| `experiments.max_variants_per_candidate` | `30` | Safety cap (prevents runaway variant explosion) |
| `experiments.journal_dir` | `data/experiments` | Directory for experiment journal files |

### 8.6 Integration Point

The experiment replay hook is inserted in `orchestrator.py` `_evaluate_candidate_inner()`, **after** the primary verdict is built and **before** `return verdict`:

```
evaluate_candidate()
    → _evaluate_candidate_inner()
        → _dispatch_agents()          # 6 agents run
        → compute_mfcs()              # Primary MFCS computation
        → consensus gate, risk veto   # Standard gates
        → _build_trade_verdict()      # Primary verdict
        → feature_logger (D87)        # ML feature logging
        → _replay_experiment_variants()  # D102: replay through 22 variants (~2ms)
            → for each variant:
                apply_overrides()     # Deep-copy Settings
                compute_mfcs()        # Re-score (~50μs)
                compute stop/sizing   # If ATR experiment (~10μs)
            → ExperimentJournal.record()  # Append to JSONL
        → return verdict              # Unchanged — primary config drives trading
```

**Performance**: 22 variants × ~50μs compute = ~1ms. Journal write ~1ms. Total overhead: ~2ms per candidate — invisible within the 15-25s LLM evaluation.

**Crash isolation**: The entire replay is wrapped in `try/except Exception` — experiment failures never affect trading decisions.

### 8.7 Post-Session Analysis

```python
from src.experiments.journal import ExperimentJournal

# Load today's experiment data
entries = ExperimentJournal.load_latest()

# Summarize by experiment
summary = ExperimentJournal.summarize(entries)
# Returns: {
#   "atr_sweep": {"atr_1.5": {"enter": 5, "skip": 3}, ...},
#   "threshold_sweep": {"thresh_0.15": {"enter": 7, "skip": 1}, ...},
#   ...
# }
```

### 8.8 What This Enables (After 5-10 Trading Days)

With 15-30 trades × 22 variants = **330-660 data points**:
- **ATR multiplier**: Compare stop distances → pick the multiplier that minimizes false stop-outs
- **MFCS threshold**: Find the threshold that maximizes expected value (selectivity vs opportunity)
- **Agent weights**: Identify which allocation produces the most discriminating MFCS spread
- **Risk lambda**: Tune risk aversion to balance caution vs opportunity
- **Confidence deflation**: Calibrate the LLM overconfidence correction factor

This data directly feeds into Tier 4 optimizations (Platt scaling, MWU weights, walk-forward optimization).

---

## 9. Session Reports

### 9.1 Architecture

**File**: `src/analysis/session_report.py`
**Storage**: `data/session_reports/session_{DATE}_{TIMESTAMP}.json`
**Format**: JSON + human-readable text summary

### 9.2 Report Contents

The `SessionReport` dataclass captures end-of-day metrics across all subsystems:

| Section | Fields |
|---|---|
| Session | date, start/end time, duration, mode (paper/live) |
| Pipeline | scans, candidates, evaluations, latency, debates |
| Execution | orders submitted/filled, fill rate, trades, slippage |
| P&L | daily PnL, wins, losses |
| Risk | circuit breaker activations, risk vetoes |
| Agents | Elo ratings, errors, latency |
| GEX | filter rejections/passes, rejection rate |
| Phase Timing (D96) | Phase 0/2 duration, Phase 3 cycles |
| Debate (D96) | skipped, no-trade, skip rate |
| Exit Events (D96) | stop-outs, smart exits |
| Agent Signals (D96) | per-agent signal distribution |
| **Experiments (D102)** | **variants tested, per-experiment enter/skip rates** |

### 9.3 Generation Methods

#### From MetricsRegistry (primary)
```python
generator = SessionReportGenerator(mode="paper")
report = generator.generate()  # Reads from MetricsRegistry singleton
generator.save(report)
```

#### From Trade Journal (D60 fallback)
When MetricsRegistry counters are unreliable (e.g., after restart loses in-memory state), the report can be derived from the journal:

```python
report = generator.generate_from_journal()
# Parses journal JSONL, counts BUY/NO_TRADE entries,
# calculates P&L, slippage, latency from journal records
```

### 9.4 Human-Readable Summary

The `summary_text()` method produces a formatted terminal block:

```
═══════════════════════════════════════════════════
  MOMENTUM-X SESSION REPORT — 2026-03-11
═══════════════════════════════════════════════════
  Mode: PAPER | Duration: 720.0 min

  PIPELINE
    Scans: 45 | Candidates: 12
    Evaluations: 8 | Latency: 3200ms
    Debates: 0 triggered, 0 → BUY

  EXECUTION
    Orders: 3 submitted, 3 filled (100%)
    Trades: 2 | Slippage: 4.2bps
    Open at close: 0

  P&L
    Daily P&L: $+127.50

  RISK
    Circuit breaker: 0 | Vetoes: 1

  PHASE TIMING
    Phase 0: 1800s | Phase 2: 300s | Phase 3 cycles: 23

  EXIT EVENTS
    Stop-outs: 1 | Smart exits: 0

  EXPERIMENTS (D102)
    Variants tested: 176 (8 candidates × 22 variants)
    atr_sweep: 4/4 enter | threshold_sweep: 3/5 enter | ...
═══════════════════════════════════════════════════
```

### 9.5 Post-Session Notification Webhook (D108)

After `report_gen.save(report)`, if `settings.session_notification_url` is configured, a compact JSON summary is POSTed:

```json
{
  "date": "2026-03-13",
  "mode": "paper",
  "pnl": 127.50,
  "trades": 3,
  "wins": 2,
  "losses": 1,
  "stop_outs": 1,
  "duration_min": 720.0,
  "evaluations": 8
}
```

- Uses `urllib.request.urlopen` (stdlib, same pattern as heartbeat webhook)
- 10-second timeout, non-fatal (all exceptions caught, logged as WARNING)
- Configure via `session_notification_url` in settings

---

## 10. Live Dashboard

### 10.1 Architecture

**File**: `src/monitoring/live_dashboard.py`
**Pattern**: Independent async task, reads MetricsRegistry every N seconds

### 10.2 Display

The dashboard prints a compact status block to the terminal at configurable intervals (default: 30 seconds):

```
==============================================================
  MOMENTUM-X LIVE  |  14:32:05 UTC  (14:32 ET)
==============================================================
  P&L: $+85.20  |  Trades: 2  |  Open: 1
  Orders: 3 sub / 3 fill  |  Slippage: 3.1bps
  Scans: 28  |  Evals: 6  |  Debates: 0 (0 BUY)
  Agent latency: 2800ms  |  Errors: 0  |  Vetoes: 1
  Circuit breaker: OK
  POSITIONS:
    JZXN: qty=666, entry=$5.00, stop=$4.73, tranches=1
==============================================================
```

### 10.3 Design Constraints

1. **Never blocks the trading loop** — runs as independent `asyncio.create_task()`
2. **All reads are O(1)** — reads MetricsRegistry snapshot, no I/O
3. **Catches all render errors** — dashboard crash never affects trading
4. **Runtime toggle** — `dashboard.toggle()` enables/disables without restart
5. **Circuit breaker integration** — shows per-service breaker status from `get_breaker_status()`

---

## 11. Console Observability (D102)

### 11.1 Phase 2 Verdict Summary

After each `evaluate_candidates()` batch completes, a summary block is logged:

```
═══ PHASE 2 VERDICT SUMMARY ═══ 8 evaluated → 2 BUY, 3 HOLD, 3 NO_TRADE | Best MFCS: 0.412 | Threshold: 0.25
  >>> AAPL: BUY (MFCS=0.412, conf=0.85) ATR stop multiplier sweep target
      TSLA: HOLD (MFCS=0.180, conf=0.60) Below threshold
      NVDA: NO_TRADE (MFCS=-0.120, conf=0.45) Bearish consensus
```

- `>>>` markers highlight BUY verdicts for quick scanning
- Shows MFCS vs threshold for every candidate
- Enables real-time monitoring of evaluation quality

### 11.2 Phase 3 Five-Minute Heartbeat

Every 5 monitoring cycles (~5 minutes), a heartbeat block is logged:

```
♥ [Phase 3] Heartbeat cycle=15 | 2 positions open | Realized P&L: $+127.50 | 10:45 ET
    AAPL: entry=$150.00 current=$153.50 P&L=+2.3% stop=$147.00 qty=100
    TSLA: entry=$200.00 current=$198.50 P&L=-0.8% stop=$194.00 qty=50
```

- Shows per-position entry/current/P&L%/stop/qty
- Enables tracking of position evolution during Phase 3
- Realized P&L includes all closed positions from the session

### 11.3 Per-Experiment Replay Breakdown

After each candidate evaluation, the experiment replay produces a per-experiment summary:

```
D102: Experiment replay for AAPL: 22 variants, 14 would-enter (primary=BUY MFCS=0.412)
  🧪 atr_sweep: 4/4 enter | MFCS range [0.412–0.412] | atr_1.5=0.412[BUY] atr_2.0=0.412[BUY] ...
  🧪 threshold_sweep: 3/5 enter | MFCS range [0.412–0.412] | thresh_0.15=0.412[BUY] thresh_0.35=0.412[BUY] ...
  🧪 weight_variants: 2/3 enter | MFCS range [0.380–0.445] | news_heavy=0.445[BUY] tech_heavy=0.380[BUY] ...
  🧪 lambda_sweep: 3/5 enter | MFCS range [0.310–0.470] | lambda_0.05=0.470[BUY] lambda_0.30=0.310[BUY] ...
  🧪 deflation_sweep: 2/5 enter | MFCS range [0.290–0.415] | deflate_0.4=0.290[---] deflate_0.8=0.415[BUY] ...
```

- Per-experiment: enter rate, MFCS range, per-variant BUY/--- flags
- Shows how parameter sensitivity affects entry decisions in real-time
- All data also persisted to experiment journal JSONL for offline analysis

---

## 12. Test Coverage Matrix

**Total**: 1,707 tests passing (D110 — up from 1,576 in D108)

### 12.1 Crash Recovery Tests

**File**: `tests/integration/test_crash_recovery.py` (8 test suites, 17+ tests)

| Suite | Tests | Scenario |
|---|---|---|
| TestStoppedOutPersistence | 4 | Stop-out cooldown survives restart, no duplicates, empty on fresh, cleared on reset |
| TestEODFlagPersistence | 3 | EOD close flag roundtrip, default false, cleared on reset |
| TestPhase0FlagPersistence | 2 | Phase 0 flag roundtrip, default false |
| TestPositionStateRecovery | 4 | Full field roundtrip, multiple positions, removal persists, partial updates |
| TestStaleStateRejection | 2 | Yesterday's state rejected, today's accepted |
| TestCorruptStateHandling | 4 | Corrupt JSON, empty file, missing D95 fields backward-compat, atomic write verification |
| TestCrashScenarios | 5 | Phase 3 crash with stop-out (March 4 scenario), crash after EOD close, multiple stop-outs, daily P&L survives, combined state integrity |
| TestSessionStateJSON | 3 | D95 fields present, version field, session date matches today |

### 12.2 Scheduling Infrastructure Tests (D101+)

**File**: `tests/unit/test_market_calendar.py` (37 tests)

| Suite | Tests | Scenario |
|---|---|---|
| TestIsWeekend | 4 | Saturday, Sunday, Monday, Friday |
| TestIsMarketHoliday | 12 | All 10 holidays for 2026, normal day, unknown year |
| TestIsEarlyClose | 4 | Day after Thanksgiving, Christmas Eve, normal day, early close time |
| TestIsTradingDay | 4 | Normal weekday, weekend, holiday, early close (still trading) |
| TestCheckMarketOpen | 4 | Normal day, weekend, holiday, early close with warning |
| TestGetHolidayName | 6 | Christmas, New Year's, Good Friday, Memorial Day, Labor Day, non-holiday |
| TestAllHolidaysAreWeekdays | 3 | Parametrized: 2025, 2026, 2027 — all observed dates fall on weekdays |

**File**: `tests/unit/test_heartbeat.py` (10 tests)

| Suite | Tests | Scenario |
|---|---|---|
| TestHeartbeatPulse | 4 | Initial state, pulse reset, pulse with phase, pulse count |
| TestHeartbeatStatus | 2 | Status alive when fresh, status fields present |
| TestHeartbeatStartStop | 2 | Start/stop lifecycle, stop without start |
| TestHeartbeatTimeout | 2 | Timeout triggers callback, pulse prevents timeout |

**File**: `tests/unit/test_health_server.py` (12 tests)

| Suite | Tests | Scenario |
|---|---|---|
| TestTradingControlState | 5 | Initial state, pause/resume, shutdown, status update, uptime |
| TestHealthControlServerIntegration | 7 | GET /health, GET /status, POST /pause, POST /resume, POST /shutdown, auth required, auth token validation |

### 12.3 Experimentation Framework Tests (D102)

**File**: `tests/unit/test_experiment_registry.py` (26 tests)

| Suite | Tests | Scenario |
|---|---|---|
| TestYAMLLoading | 11 | Load/parse YAML, active vs disabled filtering, variant count, fields, nonexistent/empty/invalid files, unloaded state |
| TestApplyOverrides | 8 | Scoring override, execution override, multi-override same/different sections, original never mutated, empty overrides, unknown section skipped, debate override |
| TestValidateOverrides | 4 | Valid overrides, unknown section, unknown field, mixed valid/invalid |
| TestProductionYAML | 3 | Load real YAML, all overrides valid, all overrides apply without error |

**File**: `tests/unit/test_experiment_replay.py` (21 tests)

| Suite | Tests | Scenario |
|---|---|---|
| TestScoringReplay | 5 | Weight variant re-scoring, threshold variant would-enter, lambda variant risk penalty, identical settings produce same MFCS, deflation variant effects |
| TestApplyOverridesIntegration | 1 | Full integration: load YAML → apply overrides → verify settings changed |
| TestExperimentJournal | 8 | Write/read roundtrip, append multiple entries, load from path, load_latest, crash-safe directory creation, crash-safe write failure, empty file handling, no files returns empty |
| TestJournalSummarize | 2 | Summary structure, enter/skip counts per variant |
| TestVariantResultModel | 3 | Required fields, optional fields (None defaults), frozen immutability |
| TestExperimentJournalEntryModel | 2 | Full construction, variant_results list integrity |

### 12.4 D108 Hardening Tests

**File**: `tests/unit/test_d108_safety.py` (8 tests)

| Suite | Tests | Scenario |
|---|---|---|
| TestStopResubmitRetry | 2 | Retry on failure then success (3 attempts), exhausts all retries (CRITICAL) |
| TestStateBackup | 2 | Backup created on save, fallback load on corrupt primary |
| TestStateDiffLogging | 1 | State diff logged on field change |
| TestPostMergeValidation | 3 | Clamps negative remaining_qty, warns stop above entry, clamps out-of-range tranches |

**File**: `tests/unit/test_d108_breakers.py` (5 tests)

| Suite | Tests | Scenario |
|---|---|---|
| TestExponentialBackoff | 3 | Timeout doubles on repeated trips, caps at MAX_RESET_TIMEOUT (300s), resets on recovery |
| TestSystemHealthGate | 2 | Healthy when all closed, unhealthy when Alpaca breaker OPEN |

**File**: `tests/unit/test_d108_observability.py` (5 tests)

| Suite | Tests | Scenario |
|---|---|---|
| TestMetricSnapshots | 2 | Snapshot writes valid JSON, rotation keeps max 50 files |
| TestSessionNotification | 2 | Notification sends POST with expected fields, failure is non-fatal |
| TestReasoningCap | 1 | 1200-char reasoning not truncated at new 1500 cap |

### 12.5 D109 Parallel Exit Strategy Tests

**File**: `tests/unit/test_exit_strategies.py` (68 tests total — 41 D109 + 27 D110)

| Suite | Tests | Scenario |
|---|---|---|
| TestVelocityEngine | 8 | 3-min rolling velocity, 4 time phases, acceleration detection, bar buffer |
| TestPullbackClassifier | 10 | State machine transitions, 30%/50% retracement thresholds, relative scaling |
| TestVolumeExhaustion | 6 | Entry-bar volume ratio, declining volume detection, threshold calibration |
| TestGratitudeExit | 7 | Time-decaying R-multiple, 3.0R→0.75R floor, 30-min window |
| TestParallelExitEngine | 10 | Any-of architecture, per-ticker state, auto-prune, 6 results, log dict |
| TestCatalystHalfLifeStrategy | 9 | FDA long half-life, social media short, exit requires time+velocity, PROMOTIONAL adjustments, unknown defaults |
| TestContagionNetwork | 9 | Same sector propagation, time decay, threshold filtering, max intensity, no self-contagion, tighten-only |
| TestAlphaDecayOracle | 6 | Positive/zero/negative alpha, decay rate, interpolation, thin alpha tighten |
| TestParallelExitEngineD110 | 3 | Returns 6 results, contagion in log dict, backward compat |

### 12.6 Key Scenario: March 4 Crash Replay

The `test_crash_during_phase3_with_stopped_out` test directly simulates the March 4 incident:

1. Buy CANF and JZXN in Phase 2
2. CANF stops out → added to cooldown
3. Process crashes
4. Process restarts
5. Verify: CANF is still in cooldown (no re-entry)
6. Verify: JZXN position data intact (entry, stop, targets)

### 12.5 Pipeline Integration Tests

**File**: `tests/integration/test_pipeline.py`

| Test | Scenario |
|---|---|
| `test_pipeline_produces_verdict` | Full pipeline: CandidateStock → 3 agents + risk → MFCS → TradeVerdict |
| `test_pipeline_risk_veto_blocks_trade` | Risk VETO → NO_TRADE regardless of other signals |

---

## 13. Failure Scenarios & Responses

### 13.1 Process Crash During Trading

| Failure | Impact | Recovery |
|---|---|---|
| Crash during Phase 0 | Research incomplete | Restart skips Phase 0 if `phase0_completed` flag set |
| Crash during Phase 2 (eval) | Partial evaluations lost | Restart re-evaluates from scanner output |
| Crash during Phase 3 (mgmt) | Positions unmanaged briefly | D64 restores positions, stops, tranches from state + Alpaca |
| Crash after EOD close | Could re-fire close | `eod_close_completed` flag prevents double-close |
| Crash with stop-out in progress | Could re-enter stopped ticker | `stopped_out_tickers` cooldown persisted per save |

### 13.2 External Service Failures

| Service | Failure Mode | Response |
|---|---|---|
| Alpaca REST API | 3 consecutive failures | Circuit breaker opens (30s initial, D108: doubles each re-trip up to 300s), no orders submitted |
| Alpaca breaker OPEN at Phase 2 | D108: `check_system_health()` returns unhealthy | Phase 2 cycle skipped, 30s sleep, retry next cycle |
| LLM Provider | 5 consecutive failures | Circuit breaker opens (60s initial, D108: backoff to 300s), evaluations skip |
| News API | 5 consecutive failures | Circuit breaker opens (60s initial, D108: backoff to 300s), news agent gets empty data |
| All services down | Multiple breakers open | Dashboard shows "OPEN: alpaca_rest, llm_provider" |

### 13.3 Stop Resubmission Failures

| Failure | System Response | Human Action |
|---|---|---|
| Cancel old stop fails | Old stop remains active (safe) | None needed |
| New stop submit fails (attempt 1-2) | D108: Retry with exponential backoff (1s, 2s) | None — auto-retrying |
| New stop submit fails (all 3 attempts) | **CRITICAL**: Position unprotected, logged with retry count | Manual stop order required |
| Ratchet-down attempt | Rejected (invariant enforced) | None — this is correct behavior |

### 13.4 Scheduler Failures

| Failure | Response |
|---|---|
| PC sleeping at 3:30 AM | Task Scheduler wakes PC (`WakeToRun`) |
| Python crashes | Scheduler retries 3× at 5-min intervals |
| Stale lock file from crashed session | Launcher detects dead PID, removes lock, starts new session |
| Zombie Python process | Launcher kills matching processes before starting |
| Missing `.env` secrets | Pre-flight check fails, session never starts |
| Import error | Pre-flight check catches, session never starts |
| Weekend trigger | DayOfWeek check → skip (exit 0) |
| **NYSE holiday** (D101) | `check_market_open()` returns False, session skips with holiday name logged |
| **Early close day** (D101) | `check_market_open()` returns True with "Early close 1:00 PM" warning |

### 13.5 Heartbeat & Health Server Failures (D101+)

| Failure | Response |
|---|---|
| Trading loop hangs (no pulse for 30 min) | Heartbeat watchdog triggers shutdown callback → SIGTERM |
| Trading loop slow (75% of timeout) | WARNING logged with seconds remaining |
| Health server port 9091 in use | Server fails to bind, logged as WARNING, trading continues |
| Health server crash | Daemon thread — no impact on trading loop |
| `/pause` request | `control.is_paused = True` — main loop skips new entries, manages existing positions |
| `/shutdown` request | `control.shutdown_requested = True` — main loop exits gracefully |
| Invalid auth token | 401 Unauthorized response, no state change |
| No auth token configured | Auth disabled (development mode), all endpoints accessible |

### 13.6 Experiment Framework Failures (D102)

| Failure | Response |
|---|---|
| Experiment YAML not found | Registry logs warning, experiments disabled for session — trading unaffected |
| Invalid experiment YAML structure | Registry logs warning, 0 experiments loaded — trading unaffected |
| `apply_overrides()` error for one variant | Variant skipped, other variants continue — no crash |
| Experiment journal write failure | Caught by try/except, logged as warning — trading unaffected |
| Experiment journal directory missing | Auto-created on first write |
| Entire replay method crashes | Wrapped in top-level try/except in orchestrator — `return verdict` still executes |
| Too many variants (>30) | Safety cap `max_variants_per_candidate` stops processing, logs debug message |
| Unknown override section/field | Skipped with warning log, remaining overrides applied |

**Key safety property**: The experiment replay is fully isolated from the trading loop. Any experiment failure results in at most a missing journal entry — never a missed trade or incorrect verdict.

---

## 14. File Reference

| File | Module | Purpose |
|---|---|---|
| `src/execution/session_state.py` | Session State | Atomic JSON persistence, **D108: .bak rotation + fallback + state diff logging** |
| `src/execution/stop_resubmitter.py` | Stop Resubmitter | Two-phase cancel/resubmit, **D108: 3x retry with exponential backoff** |
| `src/execution/position_manager.py` | Position Manager | D56/D64 sync methods, **D108: post-merge validation** |
| `src/execution/bridge.py` | Execution Bridge | TradeVerdict → Order → ManagedPosition coordination |
| `src/utils/circuit_breaker.py` | Circuit Breaker | Async-friendly per-service breakers, **D108: exponential backoff + aggregate health gate** |
| `src/monitoring/metrics.py` | Metrics Registry | O(1) metrics with JSON + Prometheus export, **D108: disk snapshots with rotation** |
| `src/monitoring/live_dashboard.py` | Live Dashboard | 30-second terminal status display |
| `src/analysis/trade_journal.py` | Trade Journal | JSONL append-only full evaluation capture |
| `src/analysis/session_report.py` | Session Report | EOD report generation (metrics or journal source), **D108: webhook notification** |
| `src/scheduling/__init__.py` | Scheduling | **D101: Package marker** |
| `src/scheduling/market_calendar.py` | Market Calendar | **D101: NYSE holidays 2025-2027, early close, pre-flight check** |
| `src/scheduling/heartbeat.py` | Heartbeat Watchdog | **D101: Internal liveness monitoring, adaptive timeout, shutdown callback** |
| `src/scheduling/health_server.py` | Health/Control Server | **D101: HTTP on port 9091, pause/resume/shutdown, auth** |
| `scripts/daily_paper_trade.ps1` | Daily Launcher | D90 safety checks, secrets, logging, toast |
| `scripts/install_scheduler.ps1` | Scheduler Install | Windows Task Scheduler setup/teardown |
| `scripts/run_trading.py` | Cross-Platform Launcher | **D101: Python replacement for PS1, holiday calendar, lock file** |
| `scripts/run_phase3.ps1` | Command Runner | Manual command execution with secrets |
| `scripts/run_phase3.sh` | Command Runner | Linux/Unix equivalent of run_phase3.ps1 |
| `src/experiments/__init__.py` | Experiments | **D102: Package marker** |
| `src/experiments/models.py` | Experiment Models | **D102: ExperimentConfig, ExperimentVariant, VariantResult, ExperimentJournalEntry** |
| `src/experiments/registry.py` | Experiment Registry | **D102: YAML loading, apply_overrides (deep copy), validate_overrides** |
| `src/experiments/journal.py` | Experiment Journal | **D102: JSONL append-only writer, load/summarize methods** |
| `data/experiments/experiments.yaml` | Experiment Config | **D102: 5 experiments, 22 variants (ATR, threshold, weight, lambda, deflation)** |
| `tests/integration/test_crash_recovery.py` | Recovery Tests | 8 suites, 17+ tests covering all crash scenarios |
| `tests/integration/test_pipeline.py` | Pipeline Tests | End-to-end pipeline with mocked LLM |
| `tests/unit/test_market_calendar.py` | Calendar Tests | **D101: 37 tests — holidays, weekends, early close, pre-flight** |
| `tests/unit/test_heartbeat.py` | Heartbeat Tests | **D101: 10 tests — pulse, timeout, lifecycle** |
| `tests/unit/test_health_server.py` | Health Server Tests | **D101: 12 tests — state, HTTP endpoints, auth** |
| `tests/unit/test_experiment_registry.py` | Registry Tests | **D102: 26 tests — YAML loading, apply_overrides, validate_overrides, production YAML** |
| `tests/unit/test_experiment_replay.py` | Replay Tests | **D102: 21 tests — scoring replay, journal roundtrip, summarize, models** |
| `tests/unit/test_d108_safety.py` | Safety Tests | **D108: 8 tests — stop retry, state backup/fallback, diff logging, post-merge validation** |
| `tests/unit/test_d108_breakers.py` | Breaker Tests | **D108: 5 tests — exponential backoff, system health gate** |
| `tests/unit/test_d108_observability.py` | Observability Tests | **D108: 5 tests — metric snapshots, session notification, reasoning cap** |
| `src/execution/exit_strategies.py` | Exit Strategies | **D109+D110: 6 parallel strategies + ContagionNetwork + AlphaDecayOracle (~1,055 LOC)** |
| `src/execution/exit_intelligence.py` | Exit Intelligence | **D109+D110: Parallel engine wiring, contagion propagation, catalyst/oracle integration** |
| `src/execution/portfolio_risk.py` | Portfolio Risk | **D110: +catalyst concentration limit (max 2 same catalyst_type at entry)** |
| `scripts/etl_sqlite.py` | SQLite ETL | **D109: Post-session JSONL→SQLite, 6 tables, 3 materialized views, idempotent** |
| `tests/unit/test_exit_strategies.py` | Exit Strategy Tests | **D109+D110: 68 tests — velocity, pullback, volume, gratitude, half-life, contagion, oracle** |
| `data/session_state.json` | Runtime | Current session state (atomic writes) |
| `data/journals/` | Runtime | JSONL trade journal files (per-session) |
| `data/experiments/` | Runtime | **D102: Experiment journal JSONL files + experiments.yaml config** |
| `src/core/adaptive_router.py` | Adaptive Router | **D112: 3-tier evaluation depth routing (INSTANT_REJECT / DETERMINISTIC_ONLY / FULL_PIPELINE)** |
| `tests/unit/test_adaptive_router.py` | Router Tests | **D112: 30 tests — tier routing, pump detection, catalyst override, metrics, edge cases** |
| `data/session_reports/` | Runtime | JSON session reports (per-session) |
| `data/signal_history/` | Runtime | **D107+D109: Exit signal history JSONL (13 signals + parallel strategy logs per cycle)** |
| `data/metrics/` | Runtime | **D108: Metric snapshots (JSON, 200-file rotation — D109: raised from 50)** |
| `logs/` | Runtime | Daily logs + transcripts |
