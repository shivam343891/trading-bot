"""
Thread-safe OHLCV candle accumulator.

Extracted into its own module so it can be imported and tested
independently of the Upstox WebSocket client.
"""
from __future__ import annotations

import threading
from datetime import datetime

import pandas as pd


class CandleBuffer:
    """Thread-safe OHLCV candle accumulator for one symbol + timeframe."""

    def __init__(self, tf_minutes: int) -> None:
        self.tf = tf_minutes
        self._lock = threading.Lock()
        self._completed: list[dict] = []
        self._current: dict | None = None

    def on_tick(
        self,
        ltp: float,
        volume: int,
        high: float,
        low: float,
        ts: datetime,
        *,
        synthetic: bool = False,
    ) -> None:
        if ltp == 0:
            return
        bucket = self._bucket(ts)
        with self._lock:
            if self._current is None or self._current["timestamp"] != bucket:
                if self._current is not None:
                    self._completed.append(dict(self._current))
                self._current = {
                    "timestamp": bucket,
                    "open": ltp,
                    "high": high,
                    "low": low,
                    "close": ltp,
                    "volume": max(volume, 0),
                    "synthetic": synthetic,
                }
            else:
                c = self._current
                c["high"] = max(c["high"], high)
                c["low"] = min(c["low"], low)
                c["close"] = ltp
                c["volume"] = max(volume, c["volume"])
                if synthetic:
                    c["synthetic"] = True

    def _bucket(self, ts: datetime) -> datetime:
        minute = (ts.minute // self.tf) * self.tf
        return ts.replace(minute=minute, second=0, microsecond=0)

    def seed(self, rows: list[dict]) -> None:
        with self._lock:
            existing_ts = {r["timestamp"] for r in self._completed}
            new_rows = [r for r in rows if r["timestamp"] not in existing_ts]
            self._completed.extend(new_rows)
            self._completed.sort(key=lambda r: r["timestamp"])

    def to_dataframe(self) -> pd.DataFrame:
        with self._lock:
            rows = list(self._completed)
        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "synthetic"])
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        if "synthetic" not in df.columns:
            df["synthetic"] = False
        return df

    def last_completed_ts(self) -> datetime | None:
        with self._lock:
            if self._completed:
                return self._completed[-1]["timestamp"]
        return None
