"""
Strategy plugin interface.

Every strategy implements Strategy and is registered in strategy/registry.py.
generate_signal() must only look at *closed* candles — the last row in the
DataFrame is the most recently *completed* candle, never the forming one.
This constraint makes backtest and live behaviour identical.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


@dataclass
class Signal:
    symbol: str
    side: str               # "BUY" or "SELL"
    entry: float
    sl: float
    target: float
    strategy_name: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class MarketContext:
    symbol: str
    current_time: datetime
    open_position: bool     # True if a position already exists for this symbol
    day_open: float         # first candle open of the session (09:15 candle)
    prev_day_close: float   # previous session's close
    has_news_today: bool = False  # wired by Phase 5 news filter


class Strategy(ABC):
    """Abstract base for all trading strategies."""

    name: str               # must be defined on every concrete subclass
    params: dict            # all tunables; populated from STRATEGY_PARAMS[name]

    @abstractmethod
    def generate_signal(
        self,
        candles_5m: pd.DataFrame,
        candles_15m: pd.DataFrame,
        context: MarketContext,
    ) -> Signal | None:
        """
        Evaluate closed candles and return a Signal, or None.

        candles_5m / candles_15m: DataFrames sorted ascending by timestamp.
        The last row is the most recently *closed* candle.
        Must not peek at the current (still-forming) candle.
        """
