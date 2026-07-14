"""
Technical indicator calculations.

Implemented with pure pandas (no pandas_ta / numba dependency) so the code
works on any Python version. All functions accept and return a pd.DataFrame
with columns: open, high, low, close, volume, and either a DatetimeIndex or
a 'timestamp' column.
"""
from __future__ import annotations

import pandas as pd


def calc_ema(df: pd.DataFrame, period: int) -> pd.DataFrame:
    col = f"ema_{period}"
    df[col] = df["close"].ewm(span=period, adjust=False).mean()
    return df


def calc_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def calc_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rolling intraday VWAP: cumsum(typical_price × volume) / cumsum(volume).
    Resets at the start of each trading day.
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df["timestamp"])

    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]

    date_group = df.index.date
    cum_pv = pv.groupby(date_group).cumsum()
    cum_vol = df["volume"].groupby(date_group).cumsum()
    df["vwap"] = cum_pv / cum_vol.replace(0, float("nan"))
    return df


def calc_avg_volume(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    df["avg_volume"] = df["volume"].rolling(window=period, min_periods=1).mean()
    return df


def enrich(
    df: pd.DataFrame,
    ema_fast: int,
    ema_slow: int,
    rsi_period: int,
    volume_period: int,
) -> pd.DataFrame:
    """Apply all indicators in one call; returns enriched DataFrame."""
    df = calc_ema(df, ema_fast)
    df = calc_ema(df, ema_slow)
    df = calc_rsi(df, rsi_period)
    df = calc_vwap(df)
    df = calc_avg_volume(df, volume_period)
    return df
