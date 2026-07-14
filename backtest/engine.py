"""
Event-driven backtest engine.

Replays candle-by-candle in timestamp order, mirroring the live loop exactly:
  1. On each 5-min close → update indicators → call strategy.generate_signal()
  2. Signals fill at next candle's open via fill_model
  3. Check SL/target on each subsequent candle (SL takes priority if both hit)
  4. Force-flatten at 15:15 at candle close

Uses the same risk/manager.py position sizing — no duplication.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Generator

import pandas as pd

from backtest import fill_model as fm
from strategy.base import MarketContext, Signal, Strategy

logger = logging.getLogger(__name__)

_MARKET_CLOSE = time(15, 15)
_MARKET_OPEN  = time(9, 15)


@dataclass
class BacktestTrade:
    symbol: str
    strategy_name: str
    side: str
    qty: int
    entry_price: float
    entry_time: datetime
    sl: float
    target: float
    exit_price: float = 0.0
    exit_time: datetime = field(default_factory=datetime.now)
    gross_pnl: float = 0.0
    txn_cost: float = 0.0
    pnl: float = 0.0
    exit_reason: str = ""


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        symbols: list[str],
        capital: float,
        max_risk_per_trade: float,
        daily_loss_limit: float,
        cost_params: dict,
        slippage_bps: float = 5.0,
    ) -> None:
        self._strategy = strategy
        self._symbols = symbols
        self._capital = capital
        self._max_risk = max_risk_per_trade
        self._daily_loss_limit = daily_loss_limit
        self._cost_params = cost_params
        self._slippage_bps = slippage_bps

    def run(
        self,
        candles_by_symbol: dict[str, pd.DataFrame],  # {symbol: 5-min df}
        candles_15m_by_symbol: dict[str, pd.DataFrame],
    ) -> list[BacktestTrade]:
        """Run the full backtest and return a list of completed trades."""
        all_trades: list[BacktestTrade] = []

        # Get sorted list of all unique 5-min timestamps across all symbols
        all_ts: set[datetime] = set()
        for df in candles_by_symbol.values():
            all_ts.update(df["timestamp"].tolist())
        sorted_ts = sorted(all_ts)

        # Per-symbol open position tracking
        open_trades: dict[str, BacktestTrade] = {}
        # Per-day state
        daily_pnl: dict[date, float] = {}
        day_halt: dict[date, bool] = {}
        day_open: dict[str, float] = {}
        prev_close: dict[str, float] = {}

        for i, ts in enumerate(sorted_ts):
            ts_date = ts.date()
            ts_time = ts.time()

            # Track day open per symbol
            for sym in self._symbols:
                df = candles_by_symbol.get(sym)
                if df is None:
                    continue
                today_rows = df[df["timestamp"].dt.date == ts_date]
                if not today_rows.empty and sym not in day_open or \
                        (sym in day_open and today_rows.iloc[0]["timestamp"] == ts):
                    day_open[sym] = float(today_rows.iloc[0]["open"])
                prev_rows = df[df["timestamp"].dt.date < ts_date]
                if not prev_rows.empty:
                    prev_close[sym] = float(prev_rows.iloc[-1]["close"])

            today_pnl = daily_pnl.get(ts_date, 0.0)
            halted = day_halt.get(ts_date, False)

            # ── Check exits for open positions ────────────────────────────────
            for sym in list(open_trades.keys()):
                trade = open_trades[sym]
                df = candles_by_symbol.get(sym)
                if df is None:
                    continue
                row = df[df["timestamp"] == ts]
                if row.empty:
                    continue
                r = row.iloc[0]

                # EOD flatten at 15:15
                if ts_time >= _MARKET_CLOSE:
                    close_side = "SELL" if trade.side == "BUY" else "BUY"
                    exit_p = fm.apply_slippage(float(r["close"]), close_side, self._slippage_bps)
                    trade = self._close_trade(trade, exit_p, ts, "eod_flatten")
                    all_trades.append(trade)
                    del open_trades[sym]
                    today_pnl += trade.pnl
                    daily_pnl[ts_date] = today_pnl
                    if today_pnl <= -self._daily_loss_limit:
                        day_halt[ts_date] = True
                    continue

                result = fm.exit_fill_price(
                    sl=trade.sl,
                    target=trade.target,
                    candle_open=float(r["open"]),
                    candle_high=float(r["high"]),
                    candle_low=float(r["low"]),
                    side=trade.side,
                    slippage_bps=self._slippage_bps,
                )
                if result:
                    exit_p, reason = result
                    trade = self._close_trade(trade, exit_p, ts, reason)
                    all_trades.append(trade)
                    del open_trades[sym]
                    today_pnl += trade.pnl
                    daily_pnl[ts_date] = today_pnl
                    if today_pnl <= -self._daily_loss_limit:
                        day_halt[ts_date] = True

            # ── Signal scan (only when not halted, market open, no position) ──
            if halted or ts_time >= _MARKET_CLOSE or ts_time < _MARKET_OPEN:
                continue

            for sym in self._symbols:
                if sym in open_trades:
                    continue

                df5 = candles_by_symbol.get(sym)
                df15 = candles_15m_by_symbol.get(sym)
                if df5 is None or df15 is None:
                    continue

                # Only use closed candles — data up to and including current ts
                hist5 = df5[df5["timestamp"] <= ts].copy()
                hist15 = df15[df15["timestamp"] <= ts].copy()
                if hist5.empty or hist15.empty:
                    continue

                ctx = MarketContext(
                    symbol=sym,
                    current_time=ts,
                    open_position=False,
                    day_open=day_open.get(sym, 0.0),
                    prev_day_close=prev_close.get(sym, 0.0),
                    has_news_today=False,
                )

                try:
                    sig = self._strategy.generate_signal(hist5, hist15, ctx)
                except Exception as exc:
                    logger.debug("Strategy error %s %s: %s", self._strategy.name, sym, exc)
                    continue

                if sig is None:
                    continue

                # Fill at next candle's open
                next_rows = df5[df5["timestamp"] > ts]
                if next_rows.empty:
                    continue
                next_row = next_rows.iloc[0]
                if next_row["timestamp"].date() != ts_date:
                    continue  # don't carry signals overnight

                fill_p = fm.entry_fill_price(
                    signal_candle_close=float(hist5.iloc[-1]["close"]),
                    next_candle_open=float(next_row["open"]),
                    side=sig.side,
                    slippage_bps=self._slippage_bps,
                )

                import math
                risk_amount = self._capital * self._max_risk
                risk_per_unit = abs(fill_p - sig.sl)
                if risk_per_unit == 0:
                    continue
                qty = max(1, math.floor(risk_amount / risk_per_unit))

                open_trades[sym] = BacktestTrade(
                    symbol=sym,
                    strategy_name=self._strategy.name,
                    side=sig.side,
                    qty=qty,
                    entry_price=fill_p,
                    entry_time=next_row["timestamp"],
                    sl=sig.sl,
                    target=sig.target,
                )
                logger.debug("BT ENTRY %s %s @%.2f qty=%d sl=%.2f tgt=%.2f ts=%s",
                             self._strategy.name, sym, fill_p, qty, sig.sl, sig.target, ts)

        # Force-close any positions still open at end of data
        for sym, trade in open_trades.items():
            df = candles_by_symbol.get(sym)
            if df is not None and not df.empty:
                last_close = float(df.iloc[-1]["close"])
                close_side = "SELL" if trade.side == "BUY" else "BUY"
                exit_p = fm.apply_slippage(last_close, close_side, self._slippage_bps)
                trade = self._close_trade(trade, exit_p, df.iloc[-1]["timestamp"], "eod_flatten")
            all_trades.append(trade)

        return all_trades

    def _close_trade(
        self,
        trade: BacktestTrade,
        exit_price: float,
        exit_time: datetime,
        exit_reason: str,
    ) -> BacktestTrade:
        entry_value = trade.qty * trade.entry_price
        exit_value = trade.qty * exit_price
        txn = fm.cost(
            entry_value=entry_value,
            exit_value=exit_value,
            side=trade.side,
            **self._cost_params,
        )
        if trade.side == "BUY":
            gross = (exit_price - trade.entry_price) * trade.qty
        else:
            gross = (trade.entry_price - exit_price) * trade.qty

        trade.exit_price = exit_price
        trade.exit_time = exit_time
        trade.gross_pnl = gross
        trade.txn_cost = txn
        trade.pnl = gross - txn
        trade.exit_reason = exit_reason
        return trade
