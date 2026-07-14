"""
Intraday mean-reversion strategy.

Fades large intraday moves when RSI is at extremes and price starts reverting.
Skips if has_news_today is True (informed moves are not faded).
Equities only — skips index symbols.
"""
from __future__ import annotations

import logging

import pandas as pd

from data.indicators import calc_rsi
from strategy.base import MarketContext, Signal, Strategy

logger = logging.getLogger(__name__)

_INDEX_PREFIXES = ("NSE_INDEX|", "BSE_INDEX|")


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(self, params: dict) -> None:
        self.params = params
        self._move_pct: float = params.get("move_pct", 0.02)        # 2% move from day open
        self._rsi_period: int = params.get("rsi_period", 14)
        self._rsi_oversold: float = params.get("rsi_oversold", 25)
        self._rsi_overbought: float = params.get("rsi_overbought", 75)
        self._retrace_frac: float = params.get("retrace_frac", 0.5)  # target = 50% retrace
        self._min_rr: float = params.get("min_rr_ratio", 1.5)

    def generate_signal(
        self,
        candles_5m: pd.DataFrame,
        candles_15m: pd.DataFrame,
        context: MarketContext,
    ) -> Signal | None:
        sym = context.symbol

        # Never fade news-driven moves
        if context.has_news_today:
            return None

        # Equities only
        if any(sym.startswith(p) for p in _INDEX_PREFIXES):
            return None

        if context.open_position:
            return None

        if len(candles_5m) < self._rsi_period + 2:
            return None

        day_open = context.day_open
        if day_open == 0:
            return None

        df = calc_rsi(candles_5m.copy(), self._rsi_period)
        df = df.dropna(subset=["rsi"])
        if len(df) < 2:
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        ltp = float(curr["close"])
        rsi = float(curr["rsi"])

        move_from_open = (ltp - day_open) / day_open

        # ── Long setup: stock sold off >= move_pct from open, RSI oversold,
        #    first candle closing back up ────────────────────────────────────
        if move_from_open <= -self._move_pct and rsi < self._rsi_oversold:
            # Confirm reversion: current close > previous close
            if curr["close"] > prev["close"]:
                # Extreme of the move = rolling low of today's session
                session_low = float(df["low"].min())
                sl = session_low
                entry = float(curr["close"])
                risk = abs(entry - sl)
                if risk == 0:
                    return None
                # Target: retrace_frac of the move back to day_open
                move_amount = abs(day_open - entry)
                raw_target = entry + move_amount * self._retrace_frac
                # Enforce minimum RR
                if (raw_target - entry) < risk * self._min_rr:
                    raw_target = entry + risk * self._min_rr
                logger.info("MR LONG %s entry=%.2f sl=%.2f target=%.2f move=%.1f%%",
                            sym, entry, sl, raw_target, move_from_open * 100)
                return Signal(
                    symbol=sym, side="BUY",
                    entry=entry, sl=sl, target=raw_target,
                    strategy_name=self.name,
                    timestamp=context.current_time,
                )

        # ── Short setup: stock surged >= move_pct from open, RSI overbought,
        #    first candle closing back down ──────────────────────────────────
        if move_from_open >= self._move_pct and rsi > self._rsi_overbought:
            if curr["close"] < prev["close"]:
                session_high = float(df["high"].max())
                sl = session_high
                entry = float(curr["close"])
                risk = abs(sl - entry)
                if risk == 0:
                    return None
                move_amount = abs(day_open - entry)
                raw_target = entry - move_amount * self._retrace_frac
                if (entry - raw_target) < risk * self._min_rr:
                    raw_target = entry - risk * self._min_rr
                logger.info("MR SHORT %s entry=%.2f sl=%.2f target=%.2f move=%.1f%%",
                            sym, entry, sl, raw_target, move_from_open * 100)
                return Signal(
                    symbol=sym, side="SELL",
                    entry=entry, sl=sl, target=raw_target,
                    strategy_name=self.name,
                    timestamp=context.current_time,
                )

        return None
