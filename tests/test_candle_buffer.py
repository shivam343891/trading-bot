"""Tests for the CandleBuffer tick aggregator in data/candle_buffer.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime
import pytest
from data.candle_buffer import CandleBuffer


def _ts(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2025, 1, 2, h, m, s)


def test_single_candle_formed():
    buf = CandleBuffer(tf_minutes=5)
    buf.on_tick(100.0, 1000, 101.0, 99.0, _ts(9, 15, 0))
    buf.on_tick(101.0, 1200, 102.0, 100.0, _ts(9, 17, 0))
    df = buf.to_dataframe()
    # No completed candle yet — still forming
    assert len(df) == 0


def test_candle_completes_on_new_bucket():
    buf = CandleBuffer(tf_minutes=5)
    buf.on_tick(100.0, 1000, 101.0, 99.0, _ts(9, 15, 0))
    buf.on_tick(101.0, 1200, 102.0, 100.0, _ts(9, 17, 0))
    # New 5-min bucket at 09:20 closes the 09:15 candle
    buf.on_tick(102.0, 1300, 103.0, 101.0, _ts(9, 20, 0))
    df = buf.to_dataframe()
    assert len(df) == 1
    candle = df.iloc[0]
    assert candle["open"] == pytest.approx(100.0)
    assert candle["close"] == pytest.approx(101.0)
    assert candle["high"] == pytest.approx(102.0)
    assert candle["low"] == pytest.approx(99.0)
    assert candle["volume"] == 1200  # last cumulative volume


def test_multiple_candles():
    buf = CandleBuffer(tf_minutes=5)
    for minute in [15, 16, 17, 18, 19]:
        buf.on_tick(100.0 + minute, 1000, 102.0, 99.0, _ts(9, minute))
    buf.on_tick(120.0, 2000, 121.0, 119.0, _ts(9, 20))
    buf.on_tick(125.0, 3000, 126.0, 124.0, _ts(9, 25))
    df = buf.to_dataframe()
    assert len(df) == 2
    assert df.iloc[0]["timestamp"].minute == 15
    assert df.iloc[1]["timestamp"].minute == 20


def test_bucket_floors_to_tf():
    buf = CandleBuffer(tf_minutes=5)
    buf.on_tick(100.0, 1000, 101.0, 99.0, _ts(9, 17, 30))  # buckets to 09:15
    buf.on_tick(101.0, 1100, 102.0, 100.0, _ts(9, 20, 0))
    df = buf.to_dataframe()
    assert len(df) == 1
    assert df.iloc[0]["timestamp"].minute == 15


def test_seed_populates_completed():
    buf = CandleBuffer(tf_minutes=5)
    rows = [
        {"timestamp": _ts(9, 15), "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.5, "volume": 1000, "synthetic": False},
        {"timestamp": _ts(9, 20), "open": 100.5, "high": 102.0,
         "low": 100.0, "close": 101.0, "volume": 1100, "synthetic": False},
    ]
    buf.seed(rows)
    df = buf.to_dataframe()
    assert len(df) == 2


def test_seed_deduplicates():
    buf = CandleBuffer(tf_minutes=5)
    row = {"timestamp": _ts(9, 15), "open": 100.0, "high": 101.0,
           "low": 99.0, "close": 100.5, "volume": 1000, "synthetic": False}
    buf.seed([row])
    buf.seed([row])  # second seed — should not duplicate
    df = buf.to_dataframe()
    assert len(df) == 1


def test_zero_ltp_ignored():
    buf = CandleBuffer(tf_minutes=5)
    buf.on_tick(0.0, 0, 0.0, 0.0, _ts(9, 15))  # should be ignored
    buf.on_tick(100.0, 1000, 101.0, 99.0, _ts(9, 16))
    buf.on_tick(101.0, 1100, 102.0, 100.0, _ts(9, 20))  # closes candle
    df = buf.to_dataframe()
    assert len(df) == 1
    assert df.iloc[0]["open"] == pytest.approx(100.0)  # not 0


def test_synthetic_flag_propagates():
    buf = CandleBuffer(tf_minutes=5)
    buf.on_tick(100.0, 1000, 101.0, 99.0, _ts(9, 15), synthetic=True)
    buf.on_tick(101.0, 1100, 102.0, 100.0, _ts(9, 20))
    df = buf.to_dataframe()
    assert bool(df.iloc[0]["synthetic"]) is True
