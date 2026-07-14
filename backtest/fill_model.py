"""
Fill model: slippage + Indian intraday equity transaction costs.

Used by both the backtest engine and broker/paper.py so P&L estimates
are honest in both contexts.

Key rules:
- Fills happen at next candle's open after signal (no look-ahead)
- Gap-through-stop: if next candle opens beyond SL, exit at that open
- Slippage applied against direction of trade on both entry and exit
"""
from __future__ import annotations

import math


def apply_slippage(price: float, side: str, slippage_bps: float) -> float:
    """
    Worsen fill price by slippage_bps basis points in the direction of the trade.
    BUY: price goes up (you pay more).
    SELL: price goes down (you receive less).
    """
    factor = slippage_bps / 10_000
    if side == "BUY":
        return price * (1 + factor)
    else:
        return price * (1 - factor)


def cost(
    entry_value: float,
    exit_value: float,
    side: str,
    brokerage_flat: float,
    brokerage_pct: float,
    stt_pct: float,
    exchange_txn_pct: float,
    sebi_charge_pct: float,
    stamp_duty_pct: float,
    gst_pct: float,
) -> float:
    """
    Total round-trip transaction cost in ₹ for an intraday equity trade.

    entry_value = qty × entry_price
    exit_value  = qty × exit_price
    side        = original trade side ("BUY" or "SELL")

    Returns a positive number representing the cost deducted from P&L.
    """
    # Brokerage: flat or % — whichever is lower, applied per leg
    entry_brokerage = min(brokerage_flat, entry_value * brokerage_pct)
    exit_brokerage = min(brokerage_flat, exit_value * brokerage_pct)
    total_brokerage = entry_brokerage + exit_brokerage

    # STT: only on sell side (for intraday, both entry and exit can be sells
    # depending on direction; for simplicity apply on the closing/sell leg)
    sell_value = exit_value if side == "BUY" else entry_value
    stt = sell_value * stt_pct

    # Exchange transaction charges — both legs
    exchange = (entry_value + exit_value) * exchange_txn_pct

    # SEBI charges — both legs
    sebi = (entry_value + exit_value) * sebi_charge_pct

    # Stamp duty — on buy leg only
    buy_value = entry_value if side == "BUY" else exit_value
    stamp = buy_value * stamp_duty_pct

    # GST on (brokerage + exchange charges)
    gst = (total_brokerage + exchange) * gst_pct

    return total_brokerage + stt + exchange + sebi + stamp + gst


def entry_fill_price(
    signal_candle_close: float,
    next_candle_open: float,
    side: str,
    slippage_bps: float,
) -> float:
    """Fill at next candle's open + slippage. Never at signal candle price."""
    return apply_slippage(next_candle_open, side, slippage_bps)


def exit_fill_price(
    sl: float,
    target: float,
    candle_open: float,
    candle_high: float,
    candle_low: float,
    side: str,
    slippage_bps: float,
) -> tuple[float, str] | None:
    """
    Check if SL or target is hit within this candle.
    Returns (fill_price, reason) or None if neither is hit.

    If the candle opens beyond SL (gap-through), exit at candle open.
    If both SL and target fall inside the candle, assume SL hit first (conservative).
    """
    if side == "BUY":
        # Gap-through SL
        if candle_open <= sl:
            return apply_slippage(candle_open, "SELL", slippage_bps), "sl_hit"
        # SL within candle
        if candle_low <= sl:
            return apply_slippage(sl, "SELL", slippage_bps), "sl_hit"
        # Target within candle (only if SL not also hit — checked above)
        if candle_high >= target:
            return apply_slippage(target, "SELL", slippage_bps), "target_hit"
    else:  # SELL (short)
        if candle_open >= sl:
            return apply_slippage(candle_open, "BUY", slippage_bps), "sl_hit"
        if candle_high >= sl:
            return apply_slippage(sl, "BUY", slippage_bps), "sl_hit"
        if candle_low <= target:
            return apply_slippage(target, "BUY", slippage_bps), "target_hit"

    return None
