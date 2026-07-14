"""Tests for risk/manager.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from unittest.mock import MagicMock
from risk.manager import RiskManager


def _make_risk(**kwargs) -> RiskManager:
    defaults = dict(
        capital=100_000,
        daily_loss_limit=2_000,
        max_risk_per_trade=0.01,
        market_close="23:59",  # effectively never closes during tests
    )
    defaults.update(kwargs)
    return RiskManager(**defaults)


def test_position_size_basic():
    risk = _make_risk()
    qty = risk.position_size(entry=100.0, sl=90.0)
    # risk_amount = 100000 * 0.01 = 1000; risk_per_unit = 10; qty = 100
    assert qty == 100


def test_position_size_min_one():
    risk = _make_risk()
    qty = risk.position_size(entry=100.0, sl=99.99)
    assert qty >= 1


def test_position_size_zero_risk():
    risk = _make_risk()
    qty = risk.position_size(entry=100.0, sl=100.0)
    assert qty == 0


def test_can_trade_initially():
    risk = _make_risk()
    assert risk.can_trade() is True


def test_halt_on_daily_loss_limit():
    risk = _make_risk()
    notifier = MagicMock()
    risk.update_daily_pnl(-2_001, notifier)
    assert risk.halted is True
    assert risk.can_trade() is False
    notifier.halt.assert_called_once()


def test_unrealized_pnl_included_in_daily_pnl():
    risk = _make_risk()
    notifier = MagicMock()
    broker = MagicMock()
    broker.place_order.return_value = "order-1"
    trade_log = MagicMock()

    risk.register_position(
        symbol="TEST", side="BUY", qty=10,
        entry_price=100.0, sl=90.0, target=120.0, trade_id=1,
    )
    # LTP is 80 — unrealized loss = (80 - 100) * 10 = -200
    risk.check_exits({"TEST": 80.0}, broker, trade_log, notifier)
    assert risk.daily_pnl < 0


def test_halt_closes_all_positions():
    risk = _make_risk(daily_loss_limit=100)
    broker = MagicMock()
    broker.place_order.return_value = "order-1"
    notifier = MagicMock()
    trade_log = MagicMock()

    risk.register_position(
        symbol="A", side="BUY", qty=10,
        entry_price=100.0, sl=90.0, target=120.0, trade_id=1,
    )
    risk.register_position(
        symbol="B", side="BUY", qty=5,
        entry_price=100.0, sl=90.0, target=120.0, trade_id=2,
    )
    # Force a loss that triggers halt
    risk._realized_pnl = -200
    risk.check_exits({"A": 50.0, "B": 50.0}, broker, trade_log, notifier)

    assert risk.halted is True
    # Both positions should have been closed
    assert broker.place_order.call_count >= 2


def test_reset_day():
    risk = _make_risk()
    risk._realized_pnl = -500
    risk.daily_pnl = -500
    risk.halted = True
    risk.reset_day()
    assert risk.daily_pnl == 0.0
    assert risk.halted is False
    assert risk._realized_pnl == 0.0


def test_exit_sl_hit():
    risk = _make_risk()
    broker = MagicMock()
    broker.place_order.return_value = "order-1"
    notifier = MagicMock()
    trade_log = MagicMock()

    risk.register_position(
        symbol="INFY", side="BUY", qty=10,
        entry_price=100.0, sl=95.0, target=115.0, trade_id=1,
    )
    risk.check_exits({"INFY": 94.0}, broker, trade_log, notifier)

    broker.place_order.assert_called_once_with("INFY", "SELL", 10, 94.0)
    trade_log.update_exit.assert_called_once()
    call_kwargs = trade_log.update_exit.call_args[1]
    assert call_kwargs["exit_reason"] == "sl_hit"
    assert call_kwargs["pnl"] == pytest.approx((94.0 - 100.0) * 10)


def test_exit_target_hit():
    risk = _make_risk()
    broker = MagicMock()
    broker.place_order.return_value = "order-1"
    notifier = MagicMock()
    trade_log = MagicMock()

    risk.register_position(
        symbol="INFY", side="BUY", qty=10,
        entry_price=100.0, sl=95.0, target=115.0, trade_id=1,
    )
    risk.check_exits({"INFY": 116.0}, broker, trade_log, notifier)

    call_kwargs = trade_log.update_exit.call_args[1]
    assert call_kwargs["exit_reason"] == "target_hit"
