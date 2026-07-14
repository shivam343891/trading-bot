"""
Paper trading broker — in-memory simulation with honest transaction costs.

Uses backtest/fill_model.py for slippage and Indian equity costs, so paper
P&L matches what the backtest reports.
"""
from __future__ import annotations

import uuid
import logging
from typing import Any

import config
from backtest.fill_model import apply_slippage, cost
from broker.base import BaseBroker

logger = logging.getLogger(__name__)


class PaperBroker(BaseBroker):
    """
    Pure in-memory paper trading engine.
    Simulates immediate fills with realistic slippage and transaction costs.
    """

    def __init__(self, capital: float) -> None:
        self.cash = capital
        self.positions: dict[str, dict[str, Any]] = {}
        self.open_orders: list[dict] = []
        self.closed_trades: list[dict] = []
        logger.info("PaperBroker initialised with capital=%.2f (costs enabled)", capital)

    # ── BaseBroker interface ──────────────────────────────────────────────────

    def place_order(self, symbol: str, side: str, qty: int, price: float) -> str:
        order_id = str(uuid.uuid4())

        # Apply slippage to fill price
        fill_price = apply_slippage(price, side, config.SLIPPAGE_BPS)

        if side == "BUY":
            entry_value = qty * fill_price
            txn_cost = cost(
                entry_value=entry_value,
                exit_value=entry_value,   # placeholder; will be recalculated on exit
                side="BUY",
                brokerage_flat=config.BROKERAGE_FLAT,
                brokerage_pct=config.BROKERAGE_PCT,
                stt_pct=config.STT_PCT,
                exchange_txn_pct=config.EXCHANGE_TXN_PCT,
                sebi_charge_pct=config.SEBI_CHARGE_PCT,
                stamp_duty_pct=config.STAMP_DUTY_PCT,
                gst_pct=config.GST_PCT,
            ) / 2  # entry leg only
            self.cash -= (entry_value + txn_cost)

            if symbol in self.positions:
                pos = self.positions[symbol]
                total_qty = pos["qty"] + qty
                pos["avg_price"] = (pos["avg_price"] * pos["qty"] + fill_price * qty) / total_qty
                pos["qty"] = total_qty
            else:
                self.positions[symbol] = {
                    "qty": qty,
                    "avg_price": fill_price,
                    "side": "BUY",
                    "sl": None,
                    "target": None,
                }
            logger.info("[PAPER] BUY  %s  qty=%d  @%.2f (fill) cost=%.2f", symbol, qty, fill_price, txn_cost)

        elif side == "SELL":
            if symbol not in self.positions and not self._has_short(symbol):
                # Opening short position
                self.positions[symbol] = {
                    "qty": qty,
                    "avg_price": fill_price,
                    "side": "SELL",
                    "sl": None,
                    "target": None,
                }
                self.cash += qty * fill_price
                logger.info("[PAPER] SELL (short open) %s  qty=%d  @%.2f", symbol, qty, fill_price)
            else:
                pos = self.positions.get(symbol)
                if pos is None:
                    logger.warning("[PAPER] SELL requested but no position in %s", symbol)
                    return order_id

                exit_value = qty * fill_price
                entry_value = qty * pos["avg_price"]
                txn_cost = cost(
                    entry_value=entry_value,
                    exit_value=exit_value,
                    side=pos["side"],
                    brokerage_flat=config.BROKERAGE_FLAT,
                    brokerage_pct=config.BROKERAGE_PCT,
                    stt_pct=config.STT_PCT,
                    exchange_txn_pct=config.EXCHANGE_TXN_PCT,
                    sebi_charge_pct=config.SEBI_CHARGE_PCT,
                    stamp_duty_pct=config.STAMP_DUTY_PCT,
                    gst_pct=config.GST_PCT,
                )
                if pos["side"] == "BUY":
                    gross_pnl = (fill_price - pos["avg_price"]) * qty
                else:
                    gross_pnl = (pos["avg_price"] - fill_price) * qty
                net_pnl = gross_pnl - txn_cost
                self.cash += exit_value - txn_cost

                self.closed_trades.append({
                    "symbol": symbol,
                    "side": pos["side"],
                    "qty": qty,
                    "entry_price": pos["avg_price"],
                    "exit_price": fill_price,
                    "gross_pnl": gross_pnl,
                    "txn_cost": txn_cost,
                    "pnl": net_pnl,
                })

                remaining = pos["qty"] - qty
                if remaining <= 0:
                    del self.positions[symbol]
                else:
                    pos["qty"] = remaining

                logger.info("[PAPER] SELL %s  qty=%d  @%.2f  net_pnl=%.2f  cost=%.2f",
                            symbol, qty, fill_price, net_pnl, txn_cost)

        self.open_orders.append({
            "order_id": order_id, "symbol": symbol, "side": side,
            "qty": qty, "price": price, "fill_price": fill_price, "status": "COMPLETE",
        })
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
        if symbol in self.positions:
            self.positions[symbol]["sl"] = sl
            self.positions[symbol]["target"] = target

    def _has_short(self, symbol: str) -> bool:
        pos = self.positions.get(symbol)
        return pos is not None and pos["side"] == "SELL"
