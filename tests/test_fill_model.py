"""Tests for backtest/fill_model.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from backtest.fill_model import (
    apply_slippage,
    cost,
    entry_fill_price,
    exit_fill_price,
)


def test_slippage_buy_worsens_price():
    price = 100.0
    filled = apply_slippage(price, "BUY", slippage_bps=5)
    assert filled > price
    assert abs(filled - 100.05) < 0.001


def test_slippage_sell_worsens_price():
    price = 100.0
    filled = apply_slippage(price, "SELL", slippage_bps=5)
    assert filled < price
    assert abs(filled - 99.95) < 0.001


def test_cost_positive():
    c = cost(
        entry_value=10_000,
        exit_value=10_200,
        side="BUY",
        brokerage_flat=20,
        brokerage_pct=0.0005,
        stt_pct=0.00025,
        exchange_txn_pct=0.0000297,
        sebi_charge_pct=0.000001,
        stamp_duty_pct=0.00003,
        gst_pct=0.18,
    )
    assert c > 0
    assert c < 100  # sanity: costs should be a small fraction


def test_entry_fill_at_next_open():
    fill = entry_fill_price(
        signal_candle_close=100.0,
        next_candle_open=101.0,
        side="BUY",
        slippage_bps=0,  # zero slippage to isolate fill logic
    )
    assert fill == pytest.approx(101.0)


def test_exit_sl_hit_long():
    result = exit_fill_price(
        sl=95.0, target=110.0,
        candle_open=99.0, candle_high=100.0, candle_low=94.0,
        side="BUY", slippage_bps=0,
    )
    assert result is not None
    price, reason = result
    assert reason == "sl_hit"
    assert price == pytest.approx(95.0)


def test_exit_target_hit_long():
    result = exit_fill_price(
        sl=95.0, target=110.0,
        candle_open=100.0, candle_high=111.0, candle_low=99.0,
        side="BUY", slippage_bps=0,
    )
    assert result is not None
    price, reason = result
    assert reason == "target_hit"
    assert price == pytest.approx(110.0)


def test_gap_through_sl_long():
    """Candle opens below SL — fill at candle open, not SL price."""
    result = exit_fill_price(
        sl=95.0, target=110.0,
        candle_open=93.0, candle_high=94.0, candle_low=92.0,
        side="BUY", slippage_bps=0,
    )
    assert result is not None
    price, reason = result
    assert reason == "sl_hit"
    assert price == pytest.approx(93.0)  # gap-through: exit at open


def test_sl_priority_over_target_same_candle():
    """When both SL and target are within one candle, SL takes priority."""
    result = exit_fill_price(
        sl=95.0, target=105.0,
        candle_open=100.0, candle_high=106.0, candle_low=94.0,
        side="BUY", slippage_bps=0,
    )
    assert result is not None
    _, reason = result
    assert reason == "sl_hit"


def test_exit_sl_hit_short():
    result = exit_fill_price(
        sl=105.0, target=90.0,
        candle_open=100.0, candle_high=106.0, candle_low=99.0,
        side="SELL", slippage_bps=0,
    )
    assert result is not None
    price, reason = result
    assert reason == "sl_hit"
    assert price == pytest.approx(105.0)


def test_no_exit_when_price_in_range():
    result = exit_fill_price(
        sl=95.0, target=110.0,
        candle_open=100.0, candle_high=102.0, candle_low=99.0,
        side="BUY", slippage_bps=0,
    )
    assert result is None
