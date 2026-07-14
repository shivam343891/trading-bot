"""
Risk manager.

Responsibilities:
- Gate new trades (daily loss limit, market hours, halt flag)
- Size positions (1% risk per trade)
- Monitor open positions for SL / target hits
- Update daily P&L and trigger halt when limit is breached
"""
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

        self.daily_pnl: float = 0.0
        self.halted: bool = False

        # Tracks SL / target for each open position keyed by symbol
        # {symbol: {"sl": float, "target": float, "side": str, "qty": int,
        #            "entry_price": float, "trade_id": int}}
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
        qty = math.floor(risk_amount / risk_per_unit)
        return max(qty, 1)

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
        """Check each open position against current LTP; close if SL/target hit."""
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
            else:  # SELL (short)
                if ltp >= sl:
                    exit_reason = "sl_hit"
                elif ltp <= target:
                    exit_reason = "target_hit"

            if exit_reason:
                close_side = "SELL" if side == "BUY" else "BUY"
                broker.place_order(symbol, close_side, qty, ltp)

                pnl = (ltp - entry_price) * qty if side == "BUY" else (entry_price - ltp) * qty
                exit_time = datetime.now()

                trade_log.update_exit(
                    trade_id=trade_id,
                    exit_price=ltp,
                    exit_time=exit_time,
                    pnl=pnl,
                    exit_reason=exit_reason,
                )
                self.update_daily_pnl(pnl, notifier)
                self.deregister_position(symbol)

                event = "TRADE_EXIT_TARGET" if exit_reason == "target_hit" else "TRADE_EXIT_SL"
                notifier.send(event, {
                    "symbol": symbol,
                    "exit_price": ltp,
                    "pnl": pnl,
                    "daily_pnl": self.daily_pnl,
                    "reason": exit_reason,
                })
                logger.info("Exit %s %s @%.2f pnl=%.2f reason=%s", symbol, side, ltp, pnl, exit_reason)

    def update_daily_pnl(self, pnl: float, notifier=None) -> None:
        self.daily_pnl += pnl
        if self.daily_pnl <= -self._daily_loss_limit and not self.halted:
            self.halted = True
            logger.warning("Daily loss limit breached — bot halted")
            if notifier:
                notifier.halt(self.daily_pnl, self._daily_loss_limit)

    def reset_day(self) -> None:
        """Call at start of each new trading day."""
        self.daily_pnl = 0.0
        self.halted = False
        self._position_meta.clear()
