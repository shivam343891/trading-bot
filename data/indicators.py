"""
Technical indicator calculations.
All functions accept and return a pd.DataFrame with columns:
  open, high, low, close, volume, timestamp (index or column).
"""
import pandas as pd
import pandas_ta as ta


def calc_ema(df: pd.DataFrame, period: int) -> pd.DataFrame:
    col = f"ema_{period}"
    df[col] = ta.ema(df["close"], length=period)
    return df


def calc_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    df["rsi"] = ta.rsi(df["close"], length=period)
    return df


def calc_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rolling intraday VWAP: cumsum(typical_price × volume) / cumsum(volume).
    Resets at the start of each trading day (first row of each date group).
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df["timestamp"])

    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]

    # Group by date so VWAP resets at 09:15 each morning
    date_group = df.index.date
    df["vwap"] = (
        pv.groupby(date_group).cumsum()
        / df["volume"].groupby(date_group).cumsum()
    )
    return df


def calc_avg_volume(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    df["avg_volume"] = df["volume"].rolling(window=period).mean()
    return df


def enrich(df: pd.DataFrame, ema_fast: int, ema_slow: int, rsi_period: int, volume_period: int) -> pd.DataFrame:
    """Apply all indicators in one call; returns enriched DataFrame."""
    df = calc_ema(df, ema_fast)
    df = calc_ema(df, ema_slow)
    df = calc_rsi(df, rsi_period)
    df = calc_vwap(df)
    df = calc_avg_volume(df, volume_period)
    return df
