import uuid
import logging
from typing import Any

from broker.base import BaseBroker

logger = logging.getLogger(__name__)


class PaperBroker(BaseBroker):
    """
    Pure in-memory paper trading engine.
    Simulates immediate fills at the price passed in — zero real API calls.
    """

    def __init__(self, capital: float) -> None:
        self.cash = capital
        self.positions: dict[str, dict[str, Any]] = {}
        self.open_orders: list[dict] = []
        self.closed_trades: list[dict] = []
        logger.info("PaperBroker initialised with capital=%.2f", capital)

    # ── BaseBroker interface ──────────────────────────────────────────────────

    def place_order(self, symbol: str, side: str, qty: int, price: float) -> str:
        order_id = str(uuid.uuid4())

        if side == "BUY":
            cost = qty * price
            self.cash -= cost
            if symbol in self.positions:
                pos = self.positions[symbol]
                total_qty = pos["qty"] + qty
                pos["avg_price"] = (pos["avg_price"] * pos["qty"] + price * qty) / total_qty
                pos["qty"] = total_qty
            else:
                self.positions[symbol] = {
                    "qty": qty,
                    "avg_price": price,
                    "side": "BUY",
                    "sl": None,
                    "target": None,
                }
            logger.info("[PAPER] BUY  %s  qty=%d  @%.2f", symbol, qty, price)

        elif side == "SELL":
            if symbol not in self.positions:
                logger.warning("[PAPER] SELL requested but no position in %s", symbol)
                return order_id

            pos = self.positions[symbol]
            proceeds = qty * price
            pnl = (price - pos["avg_price"]) * qty
            self.cash += proceeds

            closed = {
                "symbol": symbol,
                "side": "BUY",           # original side
                "qty": qty,
                "entry_price": pos["avg_price"],
                "exit_price": price,
                "pnl": pnl,
            }
            self.closed_trades.append(closed)

            remaining = pos["qty"] - qty
            if remaining <= 0:
                del self.positions[symbol]
            else:
                pos["qty"] = remaining

            logger.info("[PAPER] SELL %s  qty=%d  @%.2f  pnl=%.2f", symbol, qty, price, pnl)

        self.open_orders.append({"order_id": order_id, "symbol": symbol, "side": side, "qty": qty, "price": price, "status": "COMPLETE"})
        return order_id

    def get_positions(self) -> dict:
        return dict(self.positions)

    def get_pnl(self) -> float:
        return sum(t["pnl"] for t in self.closed_trades)

    def cancel_order(self, order_id: str) -> None:
        self.open_orders = [o for o in self.open_orders if o["order_id"] != order_id]
        logger.info("[PAPER] Cancelled order %s", order_id)

    # ── Paper-only helpers ────────────────────────────────────────────────────

    def attach_sl_target(self, symbol: str, sl: float, target: float) -> None:
        """Store SL and target on the position so RiskManager can check exits."""
        if symbol in self.positions:
            self.positions[symbol]["sl"] = sl
            self.positions[symbol]["target"] = target
