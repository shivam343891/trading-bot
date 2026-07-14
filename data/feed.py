"""
Market data feed.

Startup:
  1. Fetches historical OHLCV via Upstox REST API to pre-warm candle buffers.
  2. Connects MarketDataStreamer (WebSocket v3) for live tick aggregation.

get_candles(symbol, tf) returns a pd.DataFrame of completed OHLCV candles
for the requested timeframe (5-min or 15-min).
"""
import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import upstox_client
from upstox_client import MarketDataStreamer

logger = logging.getLogger(__name__)

# OHLCV column names as returned by Upstox historical API
_HIST_COLS = ["timestamp", "open", "high", "low", "close", "volume", "oi"]


class _CandleBuffer:
    """Thread-safe OHLCV candle accumulator for one symbol + timeframe."""

    def __init__(self, tf_minutes: int) -> None:
        self.tf = tf_minutes
        self._lock = threading.Lock()
        self._completed: list[dict] = []          # finished candles
        self._current: dict | None = None         # candle being built

    def on_tick(self, ltp: float, volume: int, high: float, low: float, ts: datetime) -> None:
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
                    "volume": volume,
                }
            else:
                c = self._current
                c["high"] = max(c["high"], high)
                c["low"] = min(c["low"], low)
                c["close"] = ltp
                c["volume"] = volume   # Upstox sends cumulative volume

    def _bucket(self, ts: datetime) -> datetime:
        """Floor timestamp to the nearest tf-minute boundary."""
        minute = (ts.minute // self.tf) * self.tf
        return ts.replace(minute=minute, second=0, microsecond=0)

    def seed(self, rows: list[dict]) -> None:
        """Pre-load historical candles (already completed)."""
        with self._lock:
            self._completed = rows

    def to_dataframe(self) -> pd.DataFrame:
        with self._lock:
            rows = list(self._completed)
        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df


class DataFeed:
    def __init__(
        self,
        api_client: upstox_client.ApiClient,
        symbols: list[str],
        candle_tf: int,
        candle_tf_slow: int,
        prewarm_days: int,
    ) -> None:
        self._api_client = api_client
        self._symbols = symbols
        self._tf_fast = candle_tf
        self._tf_slow = candle_tf_slow
        self._prewarm_days = prewarm_days
        self._history_api = upstox_client.HistoryApi(api_client)

        # buffers[(symbol, tf)] = _CandleBuffer
        self._buffers: dict[tuple[str, int], _CandleBuffer] = {}
        for sym in symbols:
            self._buffers[(sym, candle_tf)] = _CandleBuffer(candle_tf)
            self._buffers[(sym, candle_tf_slow)] = _CandleBuffer(candle_tf_slow)

        self._streamer: MarketDataStreamer | None = None
        self._stream_thread: threading.Thread | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._prewarm()
        self._connect_websocket()
        logger.info("DataFeed started — subscribed to %d symbols", len(self._symbols))

    def stop(self) -> None:
        if self._streamer:
            try:
                self._streamer.disconnect()
            except Exception:
                pass
        logger.info("DataFeed stopped")

    def get_candles(self, symbol: str, tf: int = 5) -> pd.DataFrame:
        buf = self._buffers.get((symbol, tf))
        if buf is None:
            return pd.DataFrame()
        return buf.to_dataframe()

    def get_ltp(self, symbol: str) -> float | None:
        """Return latest close price from the fast candle buffer."""
        df = self.get_candles(symbol, self._tf_fast)
        if df.empty:
            return None
        return float(df.iloc[-1]["close"])

    # ── Historical pre-warm ───────────────────────────────────────────────────

    def _prewarm(self) -> None:
        today = datetime.now(timezone.utc)
        from_date = (today - timedelta(days=self._prewarm_days)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        for sym in self._symbols:
            for tf in (self._tf_fast, self._tf_slow):
                try:
                    resp = self._history_api.get_historical_candle_data1(
                        instrument_key=sym,
                        unit="minutes",
                        interval=str(tf),
                        to_date=to_date,
                        from_date=from_date,
                        api_version="2.0",
                    )
                    candles = resp.data.candles if resp.data else []
                    rows = []
                    for c in candles:
                        # Upstox returns [timestamp, open, high, low, close, volume, oi]
                        rows.append({
                            "timestamp": pd.to_datetime(c[0]),
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": int(c[5]),
                        })
                    self._buffers[(sym, tf)].seed(rows)
                    logger.info("Pre-warmed %s tf=%dmin with %d candles", sym, tf, len(rows))
                except Exception as exc:
                    logger.warning("Pre-warm failed for %s tf=%d: %s", sym, tf, exc)

    # ── WebSocket ─────────────────────────────────────────────────────────────

    def _connect_websocket(self) -> None:
        streamer = MarketDataStreamer(self._api_client, "full")

        def on_message(message: Any) -> None:
            try:
                feeds = message.get("feeds", {})
                for sym, feed_data in feeds.items():
                    if sym not in self._symbols:
                        continue
                    ff = feed_data.get("ff", {})
                    market_ff = ff.get("marketFF", {})
                    ltpc = market_ff.get("ltpc", {})
                    ohlc = market_ff.get("marketOHLC", {}).get("ohlc", [{}])

                    ltp = float(ltpc.get("ltp", 0))
                    volume = int(market_ff.get("tv", 0))
                    high = float(ohlc[0].get("high", ltp)) if ohlc else ltp
                    low = float(ohlc[0].get("low", ltp)) if ohlc else ltp
                    ts = datetime.now()

                    for tf in (self._tf_fast, self._tf_slow):
                        buf = self._buffers.get((sym, tf))
                        if buf:
                            buf.on_tick(ltp, volume, high, low, ts)
            except Exception as exc:
                logger.debug("Tick parse error: %s", exc)

        def on_open() -> None:
            streamer.subscribe(self._symbols, "full")
            logger.info("WebSocket connected and subscribed")

        def on_close() -> None:
            logger.warning("WebSocket disconnected")

        def on_error(err: Any) -> None:
            logger.error("WebSocket error: %s", err)

        streamer.on("message", on_message)
        streamer.on("open", on_open)
        streamer.on("close", on_close)
        streamer.on("error", on_error)

        self._streamer = streamer
        self._stream_thread = threading.Thread(target=streamer.connect, daemon=True)
        self._stream_thread.start()
