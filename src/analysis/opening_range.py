"""doc 188: Opening-range continuation detector — pick the pumps that CONTINUE.

This is the deterministic implementation of the doc-187 deep-research edge — the
ONLY validated way (3-vote confirmed) to tell a gap-up that keeps running from one
that fades, within the first ~30 min:

  RANK 1  opening RVOL  = first-5-min volume / trailing-14d avg of that 5-min window
          (a THRESHOLD/RANK signal, not a smooth gradient — Zarattini/Barbon/Aziz 2024)
  RANK 2  opening-range direction = sign of the 9:30-9:35 candle; trade IN that
          direction on a break of the 5-min range high/low (DON'T fade the open)
  +VWAP   hold above VWAP = continuation; lose it = fade (a top confirmed feature)
  RANK 4  weight this most in the first ~45 min

Why this exists (Pierce's question — "why don't we trade pumps?"): pumps ARE
tradeable, but only the ~1/3 that continue, and ONLY if you can pick + fill + exit
them. This module is the PICK. It deliberately ignores the doc-187 DEBUNKED signals
(short interest alone, displayed L2 wall size, float rotation, premarket-RVOL-as-
confirmation) — trading those is the negative-expectancy trap.

Pure logic + a light tracker. Nothing here trades; callers use the signal to confirm
/ size / gate entries. Liquidity caveat (doc 187): the edge was validated on LIQUID
names; a min-dollar-volume floor is the caller's responsibility before acting on it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_NY_TZ = ZoneInfo("America/New_York")
_OPEN_ET = time(9, 30)
_OR_END_ET = time(9, 35)  # 5-minute opening range


@dataclass(frozen=True)
class OpeningRangeSignal:
    """The continuation read for one ticker at decision time."""
    opening_rvol: float          # first-5-min volume / 14d-avg of that window (or proxy)
    range_sign: int              # +1 bullish open, -1 bearish, 0 doji
    broke_high: bool             # current price > opening-range high
    broke_low: bool              # current price < opening-range low
    above_vwap: bool             # current price >= VWAP (or VWAP unknown)
    confirmed: bool              # the validated long-continuation signature is present
    score: float                 # 0..1 continuation confidence (for sizing / ranking)
    reason: str

    def as_features(self) -> dict[str, float]:
        """The subset fed to the intraday continuer (doc 184)."""
        import math
        return {
            "opening_rvol_log": math.log1p(max(self.opening_rvol, 0.0)),
            "opening_range_sign": float(self.range_sign),
            "broke_or_high": 1.0 if self.broke_high else 0.0,
        }


def in_opening_range_window(now_utc: datetime | None = None) -> bool:
    et = (now_utc or datetime.now(timezone.utc)).astimezone(_NY_TZ).time()
    return _OPEN_ET <= et < _OR_END_ET


def extract_opening_bar(bars: list[dict]) -> dict | None:
    """Build the 9:30-9:35 ET opening-range bar (high/low/open/close/volume) from
    1-min bars. Returns None if no RTH bars in the window. Robust to UTC or ET 't'."""
    o = h = l = c = None
    vol = 0.0
    for b in bars or []:
        t = b.get("t") or b.get("timestamp")
        if t is None:
            continue
        try:
            bt = datetime.fromisoformat(str(t).replace("Z", "+00:00")) if isinstance(t, str) else t
            et = bt.astimezone(_NY_TZ).time()
        except Exception:
            continue
        if not (_OPEN_ET <= et < _OR_END_ET):
            continue
        bo = b.get("o", b.get("open")); bh = b.get("h", b.get("high"))
        bl = b.get("l", b.get("low")); bc = b.get("c", b.get("close"))
        bv = b.get("v", b.get("volume")) or 0
        if o is None and bo is not None:
            o = float(bo)
        if bh is not None:
            h = float(bh) if h is None else max(h, float(bh))
        if bl is not None:
            l = float(bl) if l is None else min(l, float(bl))
        if bc is not None:
            c = float(bc)
        vol += float(bv)
    if o is None or h is None or l is None or c is None:
        return None
    return {"o": o, "h": h, "l": l, "c": c, "v": vol}


def compute_signal(
    *,
    opening_bar: dict,
    current_price: float,
    vwap: float | None = None,
    baseline_5min_volume: float | None = None,
    rvol_proxy: float | None = None,
    rvol_threshold: float = 1.0,
) -> OpeningRangeSignal:
    """Compute the continuation signal from the opening bar + live price/VWAP.

    opening_rvol uses the precise baseline (first-5-min vol / 14d avg of that window)
    when `baseline_5min_volume` is given; otherwise falls back to `rvol_proxy` (the
    scanner's RVOL) so the signal is usable before the precise baseline is wired.
    """
    o, h, l = opening_bar["o"], opening_bar["h"], opening_bar["l"]
    c, v = opening_bar["c"], opening_bar.get("v", 0.0)

    if baseline_5min_volume and baseline_5min_volume > 0:
        opening_rvol = v / baseline_5min_volume
    elif rvol_proxy is not None:
        opening_rvol = float(rvol_proxy)
    else:
        opening_rvol = 0.0

    range_sign = 1 if c > o else (-1 if c < o else 0)
    broke_high = current_price > h
    broke_low = current_price < l
    above_vwap = (vwap is None) or (vwap <= 0) or (current_price >= vwap)

    # The validated LONG-continuation signature (Zarattini RANK 1+2 + VWAP):
    #   bullish open AND price broke the 5-min high AND opening RVOL >= threshold
    #   AND holding above VWAP.
    confirmed = bool(
        range_sign > 0 and broke_high and opening_rvol >= rvol_threshold and above_vwap
    )

    # Continuation confidence (0..1) for sizing/ranking. Each validated component
    # contributes; RVOL is the heaviest (RANK 1). Capped, interpretable.
    rvol_component = min(opening_rvol / 5.0, 1.0)          # saturates by ~5x
    score = (
        0.45 * rvol_component
        + 0.25 * (1.0 if (range_sign > 0 and broke_high) else 0.0)
        + 0.20 * (1.0 if above_vwap else 0.0)
        + 0.10 * (1.0 if range_sign > 0 else 0.0)
    )
    score = round(max(0.0, min(score, 1.0)), 3)

    reason = (
        f"orvol={opening_rvol:.1f}x sign={range_sign:+d} "
        f"broke_hi={broke_high} vwap_ok={above_vwap} -> "
        f"{'CONFIRMED' if confirmed else 'unconfirmed'}"
    )
    return OpeningRangeSignal(
        opening_rvol=round(opening_rvol, 2), range_sign=range_sign,
        broke_high=broke_high, broke_low=broke_low, above_vwap=above_vwap,
        confirmed=confirmed, score=score, reason=reason,
    )


def faller_exemption_ok(
    *,
    enabled: bool,
    confirmed: bool,
    dollar_volume: float | None,
    opening_rvol: float | None,
    min_dollar_volume: float,
    min_opening_rvol: float,
) -> bool:
    """doc 191 (gate-ACTION): should a continuation-CONFIRMED name be EXEMPT from the
    faller block?

    True ONLY when ALL hold:
      - the gate-action is ENABLED (default OFF — flip only after the doc-182 grader
        confirms confirmed-continuers actually run on OUR low-float universe);
      - the opening-range signal is CONFIRMED (the validated long-continuation
        signature, doc 188);
      - the name clears a LIQUIDITY floor (dollar-volume) — the edge needs fills + a
        survivable exit; NEVER exempt the thinnest names where execution is the killer
        (doc 187 caveat);
      - opening RVOL clears a (higher-than-confirm) bar — extra conviction for a LIVE
        gate override vs. mere observation.

    Pure + deterministic (primitive args in, bool out) — testable without main.py.
    Default-OFF by ``enabled`` makes the wired call a no-op until the flag is flipped.
    """
    if not enabled or not confirmed:
        return False
    if (dollar_volume or 0.0) < min_dollar_volume:
        return False
    if (opening_rvol or 0.0) < min_opening_rvol:
        return False
    return True


def fade_short_signature_ok(
    *,
    enabled: bool,
    mfcs: float | None,
    shortable: bool,
    easy_to_borrow: bool,
    dollar_volume: float | None,
    above_vwap: bool,
    orb_confirmed: bool,
    min_mfcs: float,
    min_dollar_volume: float,
) -> bool:
    """doc 202: should we SHORT this candidate as a predictable fader?

    The selection study (doc 198-201) found the bot's high-MFCS (>=0.50) BUY picks FADE
    (4% RAN, -6.19% fwd) and the BORROWABLE ones net +9.47% short. True only when ALL hold:
      - the gate is ENABLED (default OFF);
      - MFCS >= min_mfcs (the ELITE-fade bucket — the worst longs / best shorts);
      - the name is shortable AND easy-to-borrow (un-borrowable = unshortable, doc 201);
      - it clears a liquidity floor (need fills + a survivable cover);
      - SQUEEZE CIRCUIT-BREAKER: NOT orb_confirmed AND NOT above_vwap. Never short a name
        showing the doc-188 continuation signature or holding above VWAP — those are the
        ~4% that RUN (the catastrophic short tail). Short only the ones already failing.

    Pure + deterministic (primitive args -> bool). Default-OFF by ``enabled``.
    """
    if not enabled or (mfcs or 0.0) < min_mfcs:
        return False
    if not (shortable and easy_to_borrow):
        return False
    if (dollar_volume or 0.0) < min_dollar_volume:
        return False
    if orb_confirmed or above_vwap:   # squeeze guard: it's running -> do NOT short
        return False
    return True


class OpeningRangeTracker:
    """Captures the 9:30-9:35 opening bar per ticker once, from minute bars, and
    serves the continuation signal. Lightweight in-memory; reset each session."""

    def __init__(self) -> None:
        self._bars: dict[str, dict] = {}
        self._baseline: dict[str, float] = {}  # 14d avg of first-5-min volume, optional

    def set_baseline(self, ticker: str, baseline_5min_volume: float) -> None:
        if baseline_5min_volume and baseline_5min_volume > 0:
            self._baseline[ticker] = float(baseline_5min_volume)

    def capture(self, ticker: str, bars: list[dict]) -> bool:
        """Capture the opening bar for `ticker` if not already captured. Returns
        True once captured. Call after ~9:35 ET when the bar is complete."""
        if ticker in self._bars:
            return True
        ob = extract_opening_bar(bars)
        if ob is None:
            return False
        self._bars[ticker] = ob
        return True

    def has(self, ticker: str) -> bool:
        return ticker in self._bars

    def signal(self, ticker: str, current_price: float, vwap: float | None = None,
               rvol_proxy: float | None = None,
               rvol_threshold: float = 1.0) -> OpeningRangeSignal | None:
        ob = self._bars.get(ticker)
        if ob is None:
            return None
        return compute_signal(
            opening_bar=ob, current_price=current_price, vwap=vwap,
            baseline_5min_volume=self._baseline.get(ticker),
            rvol_proxy=rvol_proxy, rvol_threshold=rvol_threshold,
        )

    def reset(self) -> None:
        self._bars.clear()
        self._baseline.clear()
