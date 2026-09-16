"""
FADER SHORT RUNNER — paper-deployment of the chronic-fader short strategy.

Per doc 101: chronic-fader gate (per-ticker continuer rate <3%, n>=5)
applied to today's high-mover catalog produces:
  S2 baseline: +1.34%/trade T+5, 67% win
  S3 + sustained close (oc >=+30%): +5.55%/trade T+5, 71% win

ARCHITECTURE:
  - Runs at ~15:50 ET (5 min before close)
  - Identifies today's high-mover candidates via Polygon snapshot
  - Cross-refs per_ticker_continuer_prior (chronic-fader gate)
  - Submits SHORT orders at market with bracket exit:
      target = entry * 0.90  (10% gain to short)
      stop   = entry * 1.10  (10% loss to short)
      time   = 5 trading days
  - Positions held overnight; managed via separate manager script

SAFETY:
  - MOMENTUM_FADER_SHORT_HALT=1 → no orders
  - FADER_SHORT_DRY_RUN=1 → log only
  - FADER_SHORT_NOTIONAL_USD per ticker (default $250)
  - FADER_SHORT_MAX_TICKERS (default 5)
  - Strict variant gate (S3 default), can relax to S2

USAGE:
    python scripts/fader_short_runner.py
    python scripts/fader_short_runner.py --variant S2  # less restrictive
    python scripts/fader_short_runner.py --dry-run

ENV:
    ALPACA_API_KEY, ALPACA_SECRET_KEY (must support shorting)
    POLYGON_API_KEY (for snapshot)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

REPO = Path(__file__).resolve().parents[1]
LOG_DIR = REPO / "logs"
STATE_DIR = REPO / "data" / "fader_short"
STATE_DIR.mkdir(parents=True, exist_ok=True)
PRIOR_PATH = REPO / "data" / "polygon_warehouse" / "derived" / "per_ticker_continuer_prior.parquet"

ET = ZoneInfo("America/New_York")
TODAY_ET = datetime.now(ET).strftime("%Y-%m-%d")
LOG_FILE = LOG_DIR / f"fader_short_{TODAY_ET}.log"

# Config
# D262 ACCOUNT ISOLATION: prefer FADER_ALPACA_* (the fader-short's OWN paper sub-account) over the
# shared prod ALPACA_*. Falls back to the shared account if no isolated key is set. (Fader notional
# is fixed/bounded ~$1,250 gross so no bankroll cap is needed; isolation is for clean P&L + to avoid
# a stray short commingling with prod.) To isolate: add FADER_ALPACA_API_KEY / _SECRET_KEY to secrets.
FADER_ACCT_ISOLATED = bool(os.environ.get("FADER_ALPACA_API_KEY", "").strip())
ALPACA_API_KEY = os.environ.get("FADER_ALPACA_API_KEY") or os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("FADER_ALPACA_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = (os.environ.get("FADER_ALPACA_BASE_URL") or os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")).rstrip("/")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/")
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
POLYGON_BASE_URL = "https://api.polygon.io"

HALT = os.environ.get("MOMENTUM_FADER_SHORT_HALT", "").strip().lower() in ("1", "true", "yes", "on")
DRY_RUN = os.environ.get("FADER_SHORT_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")
NOTIONAL_USD = float(os.environ.get("FADER_SHORT_NOTIONAL_USD", "250"))
MAX_TICKERS = int(os.environ.get("FADER_SHORT_MAX_TICKERS", "5"))
MIN_DVOL_USD = float(os.environ.get("FADER_SHORT_MIN_DVOL", "5e6"))
TARGET_PCT = float(os.environ.get("FADER_SHORT_TARGET_PCT", "10"))  # +10% short profit
STOP_PCT = float(os.environ.get("FADER_SHORT_STOP_PCT", "10"))      # -10% short loss
HOLD_DAYS = int(os.environ.get("FADER_SHORT_HOLD_DAYS", "5"))


def setup_logger():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(fmt))
    log = logging.getLogger("fader_short")
    log.handlers.clear(); log.addHandler(fh); log.addHandler(sh); log.setLevel(logging.INFO)
    return log


log = setup_logger()


@dataclass
class Candidate:
    ticker: str
    open_px: float
    last_px: float
    intraday_pct: float
    oc_pct: float
    dvol_M: float
    n_appearances: int
    cont_rate: float
    hist_avg_t5: float
    score: float  # higher = stronger short


@dataclass
class ShortPosition:
    ticker: str
    qty: int
    entry_px: float
    entry_order_id: str
    target_px: float
    stop_px: float
    target_order_id: str | None = None
    stop_order_id: str | None = None
    time_stop_date: str | None = None  # ISO date for T+HOLD_DAYS


# ── Alpaca + Polygon HTTP ─────────────────────────────────────────

class AlpacaClient:
    def __init__(self):
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
        self.headers = {"APCA-API-KEY-ID": ALPACA_API_KEY,
                         "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
                         "Content-Type": "application/json"}
        self.client = httpx.AsyncClient(headers=self.headers, timeout=30.0)  # noqa: async-leak  (closed via self.aclose())

    async def aclose(self):
        await self.client.aclose()

    async def get_account(self):
        r = await self.client.get(f"{ALPACA_BASE_URL}/v2/account")
        r.raise_for_status(); return r.json()

    async def is_shortable(self, symbol: str) -> tuple[bool, str]:
        """2026-05-12: pre-check Alpaca asset's shortable + easy_to_borrow flags.
        Returns (can_short, reason_if_not). Microcap fader candidates frequently
        fail short submission with 422 because they're HTB or NSS list. Pre-check
        avoids wasting an order POST round-trip and gives a clear log line.
        """
        try:
            r = await self.client.get(f"{ALPACA_BASE_URL}/v2/assets/{symbol}")
            r.raise_for_status()
            asset = r.json()
            shortable = bool(asset.get("shortable"))
            etb = bool(asset.get("easy_to_borrow"))
            tradable = bool(asset.get("tradable"))
            if not tradable:
                return False, "asset not tradable"
            if not shortable:
                return False, "asset not shortable (HTB / NSS)"
            if not etb:
                # Still allowed but flag it so log is clearer
                return True, "shortable but not easy-to-borrow (locate may fail)"
            return True, "ok"
        except Exception as e:
            return False, f"asset lookup failed: {e}"

    async def submit_short(self, symbol: str, qty: int, target_pct: float, stop_pct: float):
        """Submit a market sell_short order (no bracket — Alpaca paper supports OTO/OCO
        for limit but bracket-on-market-short is unreliable; we add child orders manually).
        """
        payload = {
            "symbol": symbol, "qty": str(qty), "side": "sell_short",
            "type": "market", "time_in_force": "day",
        }
        r = await self.client.post(f"{ALPACA_BASE_URL}/v2/orders", json=payload)
        r.raise_for_status(); return r.json()

    async def submit_buy_to_cover_limit(self, symbol: str, qty: int, limit_px: float):
        """GTC buy-to-cover limit order at target price (covers short at target)."""
        payload = {
            "symbol": symbol, "qty": str(qty), "side": "buy",
            "type": "limit", "time_in_force": "gtc", "limit_price": f"{limit_px:.4f}",
        }
        r = await self.client.post(f"{ALPACA_BASE_URL}/v2/orders", json=payload)
        r.raise_for_status(); return r.json()

    async def submit_buy_to_cover_stop(self, symbol: str, qty: int, stop_px: float):
        """GTC buy-to-cover stop order (covers short on adverse move)."""
        payload = {
            "symbol": symbol, "qty": str(qty), "side": "buy",
            "type": "stop", "time_in_force": "gtc", "stop_price": f"{stop_px:.4f}",
        }
        r = await self.client.post(f"{ALPACA_BASE_URL}/v2/orders", json=payload)
        r.raise_for_status(); return r.json()

    async def get_order(self, order_id: str):
        r = await self.client.get(f"{ALPACA_BASE_URL}/v2/orders/{order_id}")
        r.raise_for_status(); return r.json()

    async def list_positions(self):
        r = await self.client.get(f"{ALPACA_BASE_URL}/v2/positions")
        r.raise_for_status(); return r.json()


async def polygon_gainers() -> list[dict]:
    """Pull /v2/snapshot/.../gainers from Polygon."""
    if not POLYGON_API_KEY:
        log.warning("POLYGON_API_KEY missing - cannot pull snapshot")
        return []
    url = f"{POLYGON_BASE_URL}/v2/snapshot/locale/us/markets/stocks/gainers"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.get(url, params={"apiKey": POLYGON_API_KEY})
            r.raise_for_status()
            return r.json().get("tickers", [])
        except Exception as e:
            log.error("polygon snapshot failed: %s", e)
            return []


# ── Per-ticker prior lookup ────────────────────────────────────────

def load_prior() -> dict[str, dict]:
    if not PRIOR_PATH.exists():
        log.warning("Prior file missing: %s", PRIOR_PATH)
        return {}
    try:
        import duckdb
        con = duckdb.connect()
        rows = con.sql(f"""
            SELECT ticker, n_appearances, smoothed_continuer_rate, avg_ret_t5_pct
            FROM read_parquet('{PRIOR_PATH.as_posix()}')
        """).fetchall()
        return {r[0]: {"n": r[1], "rate": r[2], "avg_t5": r[3]} for r in rows}
    except Exception as e:
        log.error("load_prior failed: %s", e)
        return {}


# ── Candidate construction ─────────────────────────────────────────

def parse_snapshot_row(g: dict) -> dict | None:
    sym = g.get("ticker")
    if not sym: return None
    pct = float(g.get("todaysChangePerc") or 0)
    last = (g.get("lastTrade") or {}).get("p") or (g.get("day") or {}).get("c")
    open_px = (g.get("day") or {}).get("o")
    vol = (g.get("day") or {}).get("v") or 0
    if not last or not open_px or open_px <= 0: return None
    last = float(last); open_px = float(open_px); vol = float(vol)
    oc_pct = (last - open_px) / open_px
    intraday_pct = pct / 100.0
    dvol = vol * (last + open_px) / 2.0
    return {"ticker": sym, "open_px": open_px, "last_px": last,
            "intraday_pct": intraday_pct, "oc_pct": oc_pct, "dvol_M": dvol / 1e6}


def build_candidates(snapshot: list[dict], prior: dict, variant: str) -> list[Candidate]:
    """Apply variant gate to today's snapshot.

    variant in {S1, S2, S3}:
      S1: every gainer with intraday >= 30%
      S2: S1 + chronic-fader gate (rate <3% AND n >=5)
      S3: S2 + sustained close >=+30% AND dvol >$5M
    """
    cands = []
    for g in snapshot:
        s = parse_snapshot_row(g)
        if not s: continue
        # Catalog gate (must be a high-mover)
        if s["intraday_pct"] < 0.30: continue
        if s["last_px"] < 0.50 or s["last_px"] > 50: continue

        pr = prior.get(s["ticker"])
        n_app = pr["n"] if pr else 0
        rate = pr["rate"] if pr else None
        avg_t5 = pr["avg_t5"] if pr else None

        if variant in ("S2", "S3"):
            if rate is None or n_app < 5: continue
            if rate >= 3.0: continue
        if variant == "S3":
            if s["oc_pct"] < 0.30: continue
            if s["dvol_M"] < 5.0: continue

        # Score: lower rate * higher abs hist_avg_t5 = stronger short
        score = (3.0 - (rate or 21)) * abs(avg_t5 or 0)
        cands.append(Candidate(
            ticker=s["ticker"], open_px=s["open_px"], last_px=s["last_px"],
            intraday_pct=s["intraday_pct"], oc_pct=s["oc_pct"],
            dvol_M=s["dvol_M"], n_appearances=n_app, cont_rate=rate or 21,
            hist_avg_t5=avg_t5 or 0, score=score,
        ))
    cands.sort(key=lambda c: -c.score)
    return cands


# ── Trade execution ────────────────────────────────────────────────

async def submit_shorts(client: AlpacaClient, cands: list[Candidate]) -> list[ShortPosition]:
    positions = []
    for c in cands:
        qty = max(1, int(NOTIONAL_USD / max(c.last_px, 0.01)))
        target_px = c.last_px * (1 - TARGET_PCT / 100)
        stop_px = c.last_px * (1 + STOP_PCT / 100)
        log.info("ORDER: SHORT %d %s @ ~$%.2f  target=$%.2f (-%.0f%%)  stop=$%.2f (+%.0f%%)",
                 qty, c.ticker, c.last_px, target_px, TARGET_PCT, stop_px, STOP_PCT)
        log.info("        cont_rate=%.1f%%  n=%d  hist_avg_t5=%.1f%%  intra=+%.1f%%  oc=+%.1f%%",
                 c.cont_rate, c.n_appearances, c.hist_avg_t5,
                 c.intraday_pct*100, c.oc_pct*100)
        if DRY_RUN or HALT:
            log.info("  [SKIPPED - %s]", "DRY_RUN" if DRY_RUN else "HALTED")
            continue
        # 2026-05-12: pre-check shortability. Microcap fader candidates
        # routinely fail with 422 because they're HTB/NSS. Save the API
        # round-trip and log a clear reason.
        can_short, reason = await client.is_shortable(c.ticker)
        if not can_short:
            log.warning("  [SKIPPED - NOT SHORTABLE: %s] %s", reason, c.ticker)
            continue
        elif "easy-to-borrow" in reason.lower() or "etb" in reason.lower():
            # Logged but proceed (might fail at submission, but worth trying)
            log.info("  [shortability note] %s: %s", c.ticker, reason)
        try:
            short_resp = await client.submit_short(c.ticker, qty, TARGET_PCT, STOP_PCT)
            short_id = short_resp.get("id", "?")
            log.info("  SHORT submitted id=%s", short_id)
            await asyncio.sleep(2.5)
            order = await client.get_order(short_id)
            filled_qty = int(float(order.get("filled_qty", 0)))
            filled_avg = float(order.get("filled_avg_price") or c.last_px)
            if filled_qty == 0:
                log.warning("  SHORT %s not filled yet (status=%s)", c.ticker, order.get("status"))
                continue
            log.info("  SHORT filled qty=%d avg=$%.4f", filled_qty, filled_avg)
            # Submit GTC bracket
            real_target = filled_avg * (1 - TARGET_PCT / 100)
            real_stop = filled_avg * (1 + STOP_PCT / 100)
            try:
                tgt_resp = await client.submit_buy_to_cover_limit(c.ticker, filled_qty, real_target)
                stp_resp = await client.submit_buy_to_cover_stop(c.ticker, filled_qty, real_stop)
                log.info("  TARGET (cover @$%.4f) id=%s", real_target, tgt_resp.get("id"))
                log.info("  STOP   (cover @$%.4f) id=%s", real_stop, stp_resp.get("id"))
                positions.append(ShortPosition(
                    ticker=c.ticker, qty=filled_qty, entry_px=filled_avg,
                    entry_order_id=short_id,
                    target_px=real_target, stop_px=real_stop,
                    target_order_id=tgt_resp.get("id"),
                    stop_order_id=stp_resp.get("id"),
                ))
            except Exception as e:
                log.error("  bracket submit failed for %s: %s", c.ticker, e)
                positions.append(ShortPosition(
                    ticker=c.ticker, qty=filled_qty, entry_px=filled_avg,
                    entry_order_id=short_id,
                    target_px=real_target, stop_px=real_stop,
                ))
        except Exception as e:
            # 2026-05-12: include broker response body for 422 diagnostics.
            # Without this, we only saw "Client error '422 Unprocessable
            # Entity'" without the actual Alpaca reason (asset not
            # shortable / HTB / insufficient buying power / etc).
            body = ""
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    body = resp.text[:500]
                except Exception:
                    body = "(could not read body)"
            log.error("  SHORT FAILED for %s: %s%s", c.ticker, e,
                       f" | body: {body}" if body else "")
    return positions


def persist_positions(positions: list[ShortPosition]):
    p = STATE_DIR / f"open_shorts_{TODAY_ET}.json"
    p.write_text(json.dumps([asdict(pos) for pos in positions], indent=2, default=str))
    log.info("Persisted %d open shorts to %s", len(positions), p)


# ── Main ───────────────────────────────────────────────────────────

async def main_async(args) -> int:
    log.info("=" * 70)
    log.info("FADER SHORT RUNNER START - %s  variant=%s", TODAY_ET, args.variant)
    log.info("=" * 70)
    log.info("Config: notional=$%.2f  max=%d  target=%.0f%%  stop=%.0f%%  hold=%dd",
             NOTIONAL_USD, MAX_TICKERS, TARGET_PCT, STOP_PCT, HOLD_DAYS)
    log.info("Safety: HALT=%s  DRY_RUN=%s", HALT, DRY_RUN)

    if HALT:
        log.warning("MOMENTUM_FADER_SHORT_HALT=1 - observation only")

    client = AlpacaClient()
    try:
        try:
            account = await client.get_account()
            log.info("Account: %s  status=%s  equity=$%.2f",
                     account.get("account_number"), account.get("status"),
                     float(account.get("equity", 0)))
            if FADER_ACCT_ISOLATED:
                log.info("D262 account: ISOLATED (using FADER_ALPACA_* sub-account)")
            else:
                log.warning(
                    "D262 account: SHARED with prod (no FADER_ALPACA_API_KEY) -- set "
                    "FADER_ALPACA_API_KEY/_SECRET_KEY in secrets to isolate.")
        except Exception as e:
            log.error("PREFLIGHT failed: %s", e)
            return 1

        prior = load_prior()
        log.info("Prior: %d tickers loaded", len(prior))

        snapshot = await polygon_gainers()
        log.info("Snapshot: %d gainers", len(snapshot))

        cands = build_candidates(snapshot, prior, args.variant)
        log.info("Candidates after %s gate: %d", args.variant, len(cands))

        if not cands:
            log.info("No %s candidates today - exiting clean", args.variant)
            return 0

        # Print all candidates; pick top MAX_TICKERS
        for i, c in enumerate(cands, 1):
            log.info("  [%d] %s  intra=+%.1f%%  oc=+%.1f%%  dvol=$%.1fM  rate=%.1f%%  n=%d",
                     i, c.ticker, c.intraday_pct*100, c.oc_pct*100,
                     c.dvol_M, c.cont_rate, c.n_appearances)

        picks = cands[:MAX_TICKERS]
        log.info("Picking top %d of %d", len(picks), len(cands))

        positions = await submit_shorts(client, picks)
        log.info("Opened %d short position(s)", len(positions))

        if positions and not (DRY_RUN or HALT):
            persist_positions(positions)

        return 0
    finally:
        await client.aclose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["S1", "S2", "S3"], default="S3")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        global DRY_RUN; DRY_RUN = True
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        log.exception("FATAL: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
