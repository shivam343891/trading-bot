"""Tests for data/indicators.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest
from data.indicators import calc_ema, calc_rsi, calc_vwap, calc_avg_volume, enrich


def _sample_df(n: int = 30, start_price: float = 100.0) -> pd.DataFrame:
    prices = [start_price + i * 0.5 for i in range(n)]
    ts = pd.date_range("2025-01-02 09:15", periods=n, freq="5min")
    return pd.DataFrame({
        "timestamp": ts,
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
        "volume": [1000 + i * 10 for i in range(n)],
    })


def test_ema_column_added():
    df = _sample_df()
    result = calc_ema(df, 9)
    assert "ema_9" in result.columns
    assert result["ema_9"].notna().any()


def test_rsi_bounds():
    df = _sample_df(50)
    result = calc_rsi(df, 14)
    assert "rsi" in result.columns
    valid = result["rsi"].dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_vwap_positive():
    df = _sample_df(20)
    result = calc_vwap(df)
    assert "vwap" in result.columns
    assert (result["vwap"].dropna() > 0).all()


def test_vwap_resets_daily():
    """VWAP for day 2 should not be affected by day 1 data."""
    ts_day1 = pd.date_range("2025-01-02 09:15", periods=15, freq="5min")
    ts_day2 = pd.date_range("2025-01-03 09:15", periods=5, freq="5min")
    all_ts = ts_day1.tolist() + ts_day2.tolist()
    prices = list(range(100, 120))
    df = pd.DataFrame({
        "timestamp": all_ts,
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
        "volume": [1000] * 20,
    })
    result = calc_vwap(df)
    # Day 2 VWAP should be close to day 2 prices, not day 1
    day2_vwap = result[result["timestamp"].dt.date == ts_day2[0].date()]["vwap"]
    day2_close = df[df["timestamp"].dt.date == ts_day2[0].date()]["close"]
    assert abs(day2_vwap.iloc[-1] - day2_close.mean()) < 5


def test_avg_volume():
    df = _sample_df(25)
    result = calc_avg_volume(df, 20)
    assert "avg_volume" in result.columns
    assert result["avg_volume"].iloc[-1] > 0


def test_enrich_all_columns():
    df = _sample_df(30)
    result = enrich(df, ema_fast=9, ema_slow=21, rsi_period=14, volume_period=20)
    for col in ["ema_9", "ema_21", "rsi", "vwap", "avg_volume"]:
        assert col in result.columns, f"Missing column: {col}"


def test_zero_volume_vwap_no_division_error():
    df = _sample_df(5)
    df["volume"] = 0
    result = calc_vwap(df)  # should not raise
    assert "vwap" in result.columns
