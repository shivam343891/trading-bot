"""
Signal engine.

Checks 5-min candles for entry conditions filtered by a 15-min bias.
Returns a Signal dataclass or None.
"""
import logging
from dataclasses import dataclass

import pandas as pd

from data.indicators import enrich

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    symbol: str
    side: str          # "BUY" or "SELL"
    entry: float
    sl: float
    target: float


class SignalEngine:
    def __init__(
        self,
        ema_fast: int,
        ema_slow: int,
        rsi_period: int,
        volume_period: int,
        volume_multiplier: float,
        rsi_long_low: float,
        rsi_long_high: float,
        rsi_short_low: float,
        rsi_short_high: float,
        min_rr_ratio: float,
    ) -> None:
        self._ema_fast = ema_fast
        self._ema_slow = ema_slow
        self._rsi_period = rsi_period
        self._volume_period = volume_period
        self._volume_mult = volume_multiplier
        self._rsi_long_low = rsi_long_low
        self._rsi_long_high = rsi_long_high
        self._rsi_short_low = rsi_short_low
        self._rsi_short_high = rsi_short_high
        self._min_rr = min_rr_ratio

    def check(
        self,
        symbol: str,
        candles_5m: pd.DataFrame,
        candles_15m: pd.DataFrame,
        open_positions: dict,
    ) -> Signal | None:
        """Return a Signal if all conditions align, else None."""

        if symbol in open_positions:
            return None

        if len(candles_5m) < max(self._ema_slow, self._rsi_period, self._volume_period) + 2:
            return None
        if len(candles_15m) < self._ema_slow + 2:
            return None

        df5 = enrich(candles_5m.copy(), self._ema_fast, self._ema_slow, self._rsi_period, self._volume_period)
        df15 = enrich(candles_15m.copy(), self._ema_fast, self._ema_slow, self._rsi_period, self._volume_period)

        # Drop rows where indicators are NaN (warm-up period)
        df5 = df5.dropna(subset=[f"ema_{self._ema_fast}", f"ema_{self._ema_slow}", "rsi", "vwap", "avg_volume"])
        df15 = df15.dropna(subset=[f"ema_{self._ema_fast}", f"ema_{self._ema_slow}"])

        if len(df5) < 2 or df15.empty:
            return None

        # ── 15-min bias ───────────────────────────────────────────────────────
        last15 = df15.iloc[-1]
        bullish_bias = last15[f"ema_{self._ema_fast}"] > last15[f"ema_{self._ema_slow}"]
        bearish_bias = last15[f"ema_{self._ema_fast}"] < last15[f"ema_{self._ema_slow}"]

        # ── 5-min last two candles ────────────────────────────────────────────
        prev = df5.iloc[-2]
        curr = df5.iloc[-1]

        entry = curr["close"]
        rsi = curr["rsi"]
        vwap = curr["vwap"]
        avg_vol = curr["avg_volume"]
        volume_ok = curr["volume"] > self._volume_mult * avg_vol

        # ── Long setup ────────────────────────────────────────────────────────
        if bullish_bias:
            vwap_reclaim = prev["close"] < prev["vwap"] and curr["close"] > vwap
            rsi_ok = self._rsi_long_low <= rsi <= self._rsi_long_high
            if vwap_reclaim and rsi_ok and volume_ok:
                sl = float(curr["low"])
                risk = abs(entry - sl)
                if risk == 0:
                    return None
                target = entry + risk * self._min_rr
                logger.info("LONG signal %s  entry=%.2f  sl=%.2f  target=%.2f", symbol, entry, sl, target)
                return Signal(symbol=symbol, side="BUY", entry=entry, sl=sl, target=target)

        # ── Short setup ───────────────────────────────────────────────────────
        if bearish_bias:
            vwap_breakdown = prev["close"] > prev["vwap"] and curr["close"] < vwap
            rsi_ok = self._rsi_short_low <= rsi <= self._rsi_short_high
            if vwap_breakdown and rsi_ok and volume_ok:
                sl = float(curr["high"])
                risk = abs(sl - entry)
                if risk == 0:
                    return None
                target = entry - risk * self._min_rr
                logger.info("SHORT signal %s  entry=%.2f  sl=%.2f  target=%.2f", symbol, entry, sl, target)
                return Signal(symbol=symbol, side="SELL", entry=entry, sl=sl, target=target)

        return None
