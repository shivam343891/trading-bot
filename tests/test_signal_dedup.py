"""Tests for signal deduplication logic (Phase 4)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime
import pandas as pd


def test_same_candle_ts_not_reprocessed():
    """Simulate the dedup logic from main.py: same candle timestamp must not fire twice."""
    last_processed: dict[tuple[str, str], datetime] = {}
    signals_fired = 0

    candle_ts = pd.Timestamp("2025-01-02 09:30:00")

    for _ in range(3):  # pretend 3 loop iterations see the same latest closed candle
        dedup_key = ("NSE_EQ|INE009A01021", "ema_vwap_rsi")
        if last_processed.get(dedup_key) == candle_ts:
            continue  # already processed
        last_processed[dedup_key] = candle_ts
        signals_fired += 1

    assert signals_fired == 1


def test_new_candle_ts_fires_signal():
    last_processed: dict[tuple[str, str], datetime] = {}
    signals_fired = 0

    for minute in [30, 35, 40]:
        candle_ts = pd.Timestamp(f"2025-01-02 09:{minute:02d}:00")
        dedup_key = ("NSE_EQ|INE009A01021", "ema_vwap_rsi")
        if last_processed.get(dedup_key) == candle_ts:
            continue
        last_processed[dedup_key] = candle_ts
        signals_fired += 1

    assert signals_fired == 3


def test_different_strategies_independent_dedup():
    last_processed: dict[tuple[str, str], datetime] = {}
    signals_fired = 0
    candle_ts = pd.Timestamp("2025-01-02 09:30:00")

    for strategy in ["ema_vwap_rsi", "orb", "mean_reversion"]:
        dedup_key = ("NSE_EQ|INE009A01021", strategy)
        if last_processed.get(dedup_key) == candle_ts:
            continue
        last_processed[dedup_key] = candle_ts
        signals_fired += 1

    assert signals_fired == 3  # each strategy fires independently


def test_different_symbols_independent_dedup():
    last_processed: dict[tuple[str, str], datetime] = {}
    signals_fired = 0
    candle_ts = pd.Timestamp("2025-01-02 09:30:00")

    for sym in ["NSE_EQ|INE009A01021", "NSE_EQ|INE002A01018"]:
        dedup_key = (sym, "ema_vwap_rsi")
        if last_processed.get(dedup_key) == candle_ts:
            continue
        last_processed[dedup_key] = candle_ts
        signals_fired += 1

    assert signals_fired == 2
