"""
Risk manager (hardened v2).

Changes from v1:
- daily_pnl = realized + unrealized (recomputed every check_exits call)
- On halt: also closes all open positions, not just blocks new entries
- check_exits accepts current_prices for unrealized P&L calculation
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, time

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(
        self,
        capital: float,
        daily_loss_limit: float,
        max_risk_per_trade: float,
        market_close: str,
    ) -> None:
        self._capital = capital
        self._daily_loss_limit = daily_loss_limit
        self._max_risk_per_trade = max_risk_per_trade
        self._market_close: time = datetime.strptime(market_close, "%H:%M").time()

        self._realized_pnl: float = 0.0
        self.daily_pnl: float = 0.0   # realized + unrealized; public for persistence
        self.halted: bool = False

        # {symbol: {sl, target, side, qty, entry_price, trade_id}}
        self._position_meta: dict[str, dict] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def can_trade(self) -> bool:
        if self.halted:
            logger.debug("can_trade=False (halted)")
            return False
        if self.daily_pnl <= -self._daily_loss_limit:
            logger.warning("Daily loss limit hit — halting")
            self.halted = True
            return False
        if datetime.now().time() >= self._market_close:
            logger.debug("can_trade=False (market closed)")
            return False
        return True

    def position_size(self, entry: float, sl: float) -> int:
        risk_amount = self._capital * self._max_risk_per_trade
        risk_per_unit = abs(entry - sl)
        if risk_per_unit == 0:
            return 0
        return max(1, math.floor(risk_amount / risk_per_unit))

    def register_position(
        self,
        symbol: str,
        side: str,
        qty: int,
        entry_price: float,
        sl: float,
        target: float,
        trade_id: int,
    ) -> None:
        self._position_meta[symbol] = {
            "sl": sl,
            "target": target,
            "side": side,
            "qty": qty,
            "entry_price": entry_price,
            "trade_id": trade_id,
        }

    def deregister_position(self, symbol: str) -> None:
        self._position_meta.pop(symbol, None)

    def check_exits(
        self,
        current_prices: dict[str, float],
        broker,
        trade_log,
        notifier,
    ) -> None:
        """
        Check SL/target for each open position.
        Also recomputes daily_pnl = realized + unrealized on each call.
        Triggers halt (and closes all positions) if limit breached.
        """
        # Recompute unrealized P&L
        unrealized = 0.0
        for symbol, meta in self._position_meta.items():
            ltp = current_prices.get(symbol)
            if ltp is None:
                continue
            if meta["side"] == "BUY":
                unrealized += (ltp - meta["entry_price"]) * meta["qty"]
            else:
                unrealized += (meta["entry_price"] - ltp) * meta["qty"]

        self.daily_pnl = self._realized_pnl + unrealized

        # Check halt based on combined P&L
        if self.daily_pnl <= -self._daily_loss_limit and not self.halted:
            self.halted = True
            logger.warning("Daily loss limit breached (realized=%.2f unrealized=%.2f) — halting",
                           self._realized_pnl, unrealized)
            if notifier:
                notifier.halt(self.daily_pnl, self._daily_loss_limit)
            # Close all open positions immediately
            self._close_all_positions(current_prices, broker, trade_log, notifier, reason="halt")
            return

        # Normal exit checks
        for symbol, meta in list(self._position_meta.items()):
            ltp = current_prices.get(symbol)
            if ltp is None:
                continue

            sl = meta["sl"]
            target = meta["target"]
            side = meta["side"]
            qty = meta["qty"]
            entry_price = meta["entry_price"]
            trade_id = meta["trade_id"]

            exit_reason = None
            if side == "BUY":
                if ltp <= sl:
                    exit_reason = "sl_hit"
                elif ltp >= target:
                    exit_reason = "target_hit"
            else:
                if ltp >= sl:
                    exit_reason = "sl_hit"
                elif ltp <= target:
                    exit_reason = "target_hit"

            if exit_reason:
                self._execute_exit(
                    symbol=symbol, meta=meta, ltp=ltp,
                    exit_reason=exit_reason, broker=broker,
                    trade_log=trade_log, notifier=notifier,
                )

    def _execute_exit(
        self,
        symbol: str,
        meta: dict,
        ltp: float,
        exit_reason: str,
        broker,
        trade_log,
        notifier,
    ) -> None:
        side = meta["side"]
        qty = meta["qty"]
        entry_price = meta["entry_price"]
        trade_id = meta["trade_id"]

        close_side = "SELL" if side == "BUY" else "BUY"
        try:
            broker.place_order(symbol, close_side, qty, ltp)
        except Exception as exc:
            logger.error("Exit order failed for %s: %s", symbol, exc)
            return

        pnl = (ltp - entry_price) * qty if side == "BUY" else (entry_price - ltp) * qty
        self._realized_pnl += pnl
        self.daily_pnl = self._realized_pnl  # unrealized is now 0 for this position
        self.deregister_position(symbol)

        try:
            trade_log.update_exit(
                trade_id=trade_id,
                exit_price=ltp,
                exit_time=datetime.now(),
                pnl=pnl,
                exit_reason=exit_reason,
            )
        except Exception as exc:
            logger.error("Trade log update failed: %s", exc)

        event = "TRADE_EXIT_TARGET" if exit_reason == "target_hit" else "TRADE_EXIT_SL"
        try:
            notifier.send(event, {
                "symbol": symbol,
                "exit_price": ltp,
                "pnl": pnl,
                "daily_pnl": self.daily_pnl,
                "reason": exit_reason,
            })
        except Exception as exc:
            logger.warning("Notifier failed on exit: %s", exc)

        logger.info("Exit %s %s @%.2f pnl=%.2f reason=%s", symbol, side, ltp, pnl, exit_reason)

    def _close_all_positions(
        self,
        current_prices: dict[str, float],
        broker,
        trade_log,
        notifier,
        reason: str = "halt",
    ) -> None:
        for symbol, meta in list(self._position_meta.items()):
            ltp = current_prices.get(symbol, meta["entry_price"])
            self._execute_exit(
                symbol=symbol, meta=meta, ltp=ltp,
                exit_reason=reason, broker=broker,
                trade_log=trade_log, notifier=notifier,
            )

    def update_daily_pnl(self, pnl: float, notifier=None) -> None:
        """Called after EOD flatten trades to accumulate realized P&L."""
        self._realized_pnl += pnl
        self.daily_pnl = self._realized_pnl
        if self.daily_pnl <= -self._daily_loss_limit and not self.halted:
            self.halted = True
            logger.warning("Daily loss limit breached — bot halted")
            if notifier:
                notifier.halt(self.daily_pnl, self._daily_loss_limit)

    def reset_day(self) -> None:
        self._realized_pnl = 0.0
        self.daily_pnl = 0.0
        self.halted = False
        self._position_meta.clear()
