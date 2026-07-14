from abc import ABC, abstractmethod


class BaseBroker(ABC):
    """Abstract broker interface — paper.py and upstox.py both implement this."""

    @abstractmethod
    def place_order(self, symbol: str, side: str, qty: int, price: float) -> str:
        """
        Place an order.

        side: "BUY" or "SELL"
        Returns an order_id string.
        """

    @abstractmethod
    def get_positions(self) -> dict:
        """
        Return current open positions.

        Format: {symbol: {"qty": int, "avg_price": float, "side": str, "sl": float, "target": float}}
        """

    @abstractmethod
    def get_pnl(self) -> float:
        """Return total realised P&L for the session."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        """Cancel a pending order by order_id."""
