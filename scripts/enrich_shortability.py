#!/usr/bin/env python3
"""D196: Enrich labeled scenarios with Alpaca shortability data.

For each labeled scenario in data/labeled_scenarios.json, populates the
``is_shortable`` field using:
  1. Live Alpaca ``GET /v2/assets/{symbol}`` → ``shortable`` + ``easy_to_borrow``
  2. Heuristic fallback for delisted / unavailable stocks:
       price < $5 AND dollar_volume < $1M → assumed NOT shortable

Usage:
    python scripts/enrich_shortability.py
    python scripts/enrich_shortability.py --scenarios data/labeled_scenarios.json
    python scripts/enrich_shortability.py --dry-run   # prints changes, does not write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Heuristic thresholds ──────────────────────────────────────────────────────
_HEURISTIC_PRICE_THRESHOLD = 5.0       # stocks below $5 → likely not shortable
_HEURISTIC_DOLVOL_THRESHOLD = 1_000_000  # dollar volume < $1M → likely not shortable

# ── Default scenarios path ────────────────────────────────────────────────────
_DEFAULT_SCENARIOS = _ROOT / "data" / "labeled_scenarios.json"


# =============================================================================
# Alpaca shortability lookup
# =============================================================================


async def lookup_shortability(ticker: str, alpaca_client) -> tuple[bool, str]:
    """Check whether a ticker is currently shortable on Alpaca.

    Args:
        ticker: Stock symbol.
        alpaca_client: AlpacaClient instance.

    Returns:
        (is_shortable, source) where source is "alpaca_live" or "error".
    """
    try:
        asset = await alpaca_client.get_asset(ticker)
        shortable = bool(asset.get("shortable", False))
        etb = bool(asset.get("easy_to_borrow", False))
        return shortable and etb, "alpaca_live"
    except Exception as exc:
        logger.debug("Alpaca lookup failed for %s: %s", ticker, exc)
        return False, "error"


def heuristic_shortable(entry_price: float, dollar_volume: float) -> tuple[bool, str]:
    """Estimate shortability from price + liquidity heuristic.

    Stocks below $5 with under $1M daily dollar volume are almost
    universally not shortable on retail brokers due to locate scarcity.

    Returns:
        (is_shortable, source) where source is "heuristic".
    """
    if entry_price > 0 and entry_price < _HEURISTIC_PRICE_THRESHOLD:
        if dollar_volume < _HEURISTIC_DOLVOL_THRESHOLD:
            return False, "heuristic_low_price_low_dolvol"
    return True, "heuristic_default"


# =============================================================================
# Main enrichment loop
# =============================================================================


async def enrich_scenarios(
    scenarios: list[dict],
    use_alpaca: bool = True,
) -> tuple[list[dict], int]:
    """Enrich scenarios list with is_shortable field.

    Args:
        scenarios: List of scenario dicts loaded from JSON.
        use_alpaca: Whether to call Alpaca live API (requires env credentials).

    Returns:
        (enriched_scenarios, changed_count)
    """
    alpaca_client = None
    if use_alpaca:
        try:
            from src.data.alpaca_client import AlpacaClient, AlpacaConfig

            api_key = os.environ.get("ALPACA_API_KEY", "")
            api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
            if api_key and api_secret:
                config = AlpacaConfig(api_key=api_key, api_secret=api_secret)
                alpaca_client = AlpacaClient(config)
                logger.info("Alpaca client initialised — will check live shortability")
            else:
                logger.warning(
                    "ALPACA_API_KEY / ALPACA_SECRET_KEY not set — "
                    "falling back to heuristic shortability"
                )
        except Exception as exc:
            logger.warning("Could not initialise AlpacaClient: %s — using heuristic", exc)

    enriched = []
    changed = 0

    for i, s in enumerate(scenarios):
        ticker = s.get("ticker", "")
        entry_price = float(s.get("entry_price", 0.0))
        dollar_volume = float(s.get("dollar_volume", 0.0))
        old_value = s.get("is_shortable", True)

        new_value: bool
        source: str

        if alpaca_client is not None:
            new_value, source = await lookup_shortability(ticker, alpaca_client)
        else:
            new_value, source = heuristic_shortable(entry_price, dollar_volume)

        enriched_scenario = dict(s)
        enriched_scenario["is_shortable"] = new_value

        if new_value != old_value:
            changed += 1
            logger.info(
                "[%d/%d] %s: is_shortable %s → %s (source=%s)",
                i + 1, len(scenarios), ticker, old_value, new_value, source,
            )
        else:
            logger.debug(
                "[%d/%d] %s: is_shortable unchanged=%s (source=%s)",
                i + 1, len(scenarios), ticker, new_value, source,
            )

        enriched.append(enriched_scenario)

    if alpaca_client is not None:
        try:
            await alpaca_client.close()
        except Exception:
            pass

    return enriched, changed


# =============================================================================
# CLI
# =============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="D196: Enrich labeled scenarios with Alpaca shortability data"
    )
    parser.add_argument(
        "--scenarios",
        default=str(_DEFAULT_SCENARIOS),
        help=f"Path to labeled scenarios JSON (default: {_DEFAULT_SCENARIOS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without writing to disk",
    )
    parser.add_argument(
        "--no-alpaca",
        action="store_true",
        help="Skip Alpaca API calls; use heuristic only",
    )
    args = parser.parse_args()

    path = Path(args.scenarios)
    if not path.exists():
        print(f"ERROR: scenarios file not found: {path}", file=sys.stderr)
        print(
            "Tip: run the arena first to generate labeled_scenarios.json, or "
            "provide a path with --scenarios",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        scenarios = json.load(f)

    print(f"Loaded {len(scenarios)} scenarios from {path}")

    enriched, changed = asyncio.run(
        enrich_scenarios(scenarios, use_alpaca=not args.no_alpaca)
    )

    print(f"Shortability enrichment complete: {changed} scenarios changed")

    if args.dry_run:
        print("DRY RUN — not writing to disk")
        for s in enriched:
            if s.get("is_shortable") is False:
                print(f"  NOT shortable: {s.get('ticker')}  price={s.get('entry_price')}  "
                      f"dolvol={s.get('dollar_volume')}")
        return

    # Atomic write: write to .tmp then rename
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2)
    tmp_path.replace(path)
    print(f"Written enriched scenarios to {path}")


if __name__ == "__main__":
    main()
