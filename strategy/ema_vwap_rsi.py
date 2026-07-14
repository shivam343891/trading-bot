"""
EMA + VWAP + RSI strategy (v1 logic, now implementing Strategy ABC).

Bias: 15-min EMA9 vs EMA21.
Entry: VWAP reclaim/breakdown + RSI zone + volume surge on 5-min.
"""
from __future__ import annotations

import logging

import pandas as pd

from data.indicators import enrich
from strategy.base import MarketContext, Signal, Strategy

logger = logging.getLogger(__name__)


class EmaVwapRsiStrategy(Strategy):
    name = "ema_vwap_rsi"

    def __init__(self, params: dict) -> None:
        self.params = params
        self._ema_fast = params["ema_fast"]
        self._ema_slow = params["ema_slow"]
        self._rsi_period = params["rsi_period"]
        self._volume_period = params["volume_period"]
        self._volume_mult = params["volume_multiplier"]
        self._rsi_long_low = params["rsi_long_low"]
        self._rsi_long_high = params["rsi_long_high"]
        self._rsi_short_low = params["rsi_short_low"]
        self._rsi_short_high = params["rsi_short_high"]
        self._min_rr = params["min_rr_ratio"]

    def generate_signal(
        self,
        candles_5m: pd.DataFrame,
        candles_15m: pd.DataFrame,
        context: MarketContext,
    ) -> Signal | None:
        if context.open_position:
            return None

        min_rows = max(self._ema_slow, self._rsi_period, self._volume_period) + 2
        if len(candles_5m) < min_rows or len(candles_15m) < self._ema_slow + 2:
            return None

        df5 = enrich(candles_5m.copy(), self._ema_fast, self._ema_slow, self._rsi_period, self._volume_period)
        df15 = enrich(candles_15m.copy(), self._ema_fast, self._ema_slow, self._rsi_period, self._volume_period)

        df5 = df5.dropna(subset=[f"ema_{self._ema_fast}", f"ema_{self._ema_slow}", "rsi", "vwap", "avg_volume"])
        df15 = df15.dropna(subset=[f"ema_{self._ema_fast}", f"ema_{self._ema_slow}"])

        if len(df5) < 2 or df15.empty:
            return None

        last15 = df15.iloc[-1]
        bullish_bias = last15[f"ema_{self._ema_fast}"] > last15[f"ema_{self._ema_slow}"]
        bearish_bias = last15[f"ema_{self._ema_fast}"] < last15[f"ema_{self._ema_slow}"]

        prev = df5.iloc[-2]
        curr = df5.iloc[-1]

        entry = float(curr["close"])
        rsi = float(curr["rsi"])
        vwap = float(curr["vwap"])
        avg_vol = float(curr["avg_volume"])
        volume_ok = curr["volume"] > self._volume_mult * avg_vol

        if bullish_bias:
            vwap_reclaim = prev["close"] < prev["vwap"] and curr["close"] > vwap
            rsi_ok = self._rsi_long_low <= rsi <= self._rsi_long_high
            if vwap_reclaim and rsi_ok and volume_ok:
                sl = float(curr["low"])
                risk = abs(entry - sl)
                if risk == 0:
                    return None
                target = entry + risk * self._min_rr
                logger.info("LONG signal %s entry=%.2f sl=%.2f target=%.2f", context.symbol, entry, sl, target)
                return Signal(
                    symbol=context.symbol, side="BUY",
                    entry=entry, sl=sl, target=target,
                    strategy_name=self.name,
                    timestamp=context.current_time,
                )

        if bearish_bias:
            vwap_breakdown = prev["close"] > prev["vwap"] and curr["close"] < vwap
            rsi_ok = self._rsi_short_low <= rsi <= self._rsi_short_high
            if vwap_breakdown and rsi_ok and volume_ok:
                sl = float(curr["high"])
                risk = abs(sl - entry)
                if risk == 0:
                    return None
                target = entry - risk * self._min_rr
                logger.info("SHORT signal %s entry=%.2f sl=%.2f target=%.2f", context.symbol, entry, sl, target)
                return Signal(
                    symbol=context.symbol, side="SELL",
                    entry=entry, sl=sl, target=target,
                    strategy_name=self.name,
                    timestamp=context.current_time,
                )

        return None
