"""
Opening Range Breakout (ORB) strategy.

Opening range = high/low of 09:15–09:45 (parameterizable via orb_minutes).
Entry: 5-min candle *closes* above OR-high (long) or below OR-low (short).
Day filters: gap filter + relative volume at OR time.
Max one ORB trade per symbol per day.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time

import pandas as pd

from strategy.base import MarketContext, Signal, Strategy

logger = logging.getLogger(__name__)


class OrbStrategy(Strategy):
    name = "orb"

    def __init__(self, params: dict) -> None:
        self.params = params
        self._orb_minutes: int = params.get("orb_minutes", 30)
        self._gap_min: float = params.get("gap_min", 0.003)        # 0.3%
        self._rvol_min: float = params.get("rvol_min", 1.3)
        self._vol_lookback: int = params.get("vol_lookback", 20)   # days for avg vol
        self._min_rr: float = params.get("min_rr_ratio", 1.5)

        # Per-day state: symbol -> date traded
        self._traded_today: dict[str, date] = {}

    def generate_signal(
        self,
        candles_5m: pd.DataFrame,
        candles_15m: pd.DataFrame,
        context: MarketContext,
    ) -> Signal | None:
        sym = context.symbol
        today = context.current_time.date()

        if context.open_position:
            return None

        # Max one ORB trade per symbol per day
        if self._traded_today.get(sym) == today:
            return None

        if candles_5m.empty:
            return None

        # ── Gap filter ────────────────────────────────────────────────────────
        if context.prev_day_close > 0:
            gap_pct = abs(context.day_open - context.prev_day_close) / context.prev_day_close
            if gap_pct < self._gap_min:
                return None

        # ── Build opening range from today's candles within OR window ─────────
        session_start = datetime.combine(today, time(9, 15))
        or_end = datetime.combine(today, time(9, 15)) .replace(
            minute=15 + self._orb_minutes
        )
        # Normalise: ensure timestamp column exists and is datetime
        df = candles_5m.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df.index = pd.to_datetime(df["timestamp"])
            else:
                return None

        today_candles = df[df.index.date == today]
        or_candles = today_candles[today_candles.index < or_end]

        if or_candles.empty:
            return None

        or_high = float(or_candles["high"].max())
        or_low = float(or_candles["low"].min())
        or_width = or_high - or_low
        if or_width == 0:
            return None

        # ── Relative volume filter at OR completion ───────────────────────────
        or_vol = float(or_candles["volume"].sum())
        # Use all candles in same time window across history for 20-day avg
        or_time_mask = (
            (df.index.time >= time(9, 15)) &
            (df.index.time < or_end.time())
        )
        hist_vol_by_day = (
            df[or_time_mask]
            .groupby(df[or_time_mask].index.date)["volume"]
            .sum()
        )
        # Exclude today from the historical average
        hist_vol_by_day = hist_vol_by_day[hist_vol_by_day.index != today]
        if len(hist_vol_by_day) >= 5:
            avg_or_vol = float(hist_vol_by_day.tail(self._vol_lookback).mean())
            rvol = or_vol / avg_or_vol if avg_or_vol > 0 else 0.0
            if rvol < self._rvol_min:
                logger.debug("ORB %s: rvol %.2f < %.2f threshold, skipping", sym, rvol, self._rvol_min)
                return None

        # ── Signal candle: first 5-min close that breaks OR after OR window ──
        post_or = today_candles[today_candles.index >= or_end]
        if post_or.empty:
            return None

        last = post_or.iloc[-1]
        entry = float(last["close"])

        if entry > or_high:
            # Long breakout
            sl = or_low
            risk = abs(entry - sl)
            if risk == 0:
                return None
            target = entry + risk * self._min_rr
            self._traded_today[sym] = today
            logger.info("ORB LONG %s entry=%.2f or_high=%.2f sl=%.2f target=%.2f",
                        sym, entry, or_high, sl, target)
            return Signal(
                symbol=sym, side="BUY",
                entry=entry, sl=sl, target=target,
                strategy_name=self.name,
                timestamp=context.current_time,
            )

        if entry < or_low:
            # Short breakdown
            sl = or_high
            risk = abs(sl - entry)
            if risk == 0:
                return None
            target = entry - risk * self._min_rr
            self._traded_today[sym] = today
            logger.info("ORB SHORT %s entry=%.2f or_low=%.2f sl=%.2f target=%.2f",
                        sym, entry, or_low, sl, target)
            return Signal(
                symbol=sym, side="SELL",
                entry=entry, sl=sl, target=target,
                strategy_name=self.name,
                timestamp=context.current_time,
            )

        return None
