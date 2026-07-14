"""
Market data feed (hardened v2).

Changes from v1:
- Auto-reconnect with exponential backoff + gap-fill via REST after reconnect
- Ticks bucketed by exchange timestamp inside payload, not local clock
- Candles marked synthetic=True when gap-filled; signals never use synthetic candles
- Zero-volume candle guard (VWAP/avg-vol calculations skip them)
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import upstox_client
from upstox_client import MarketDataStreamerV3 as MarketDataStreamer

from data.candle_buffer import CandleBuffer as _CandleBuffer  # re-export for backwards compat

logger = logging.getLogger(__name__)

_MAX_RECONNECT_WAIT = 60   # seconds cap for backoff


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

        self._buffers: dict[tuple[str, int], _CandleBuffer] = {}
        for sym in symbols:
            self._buffers[(sym, candle_tf)] = _CandleBuffer(candle_tf)
            self._buffers[(sym, candle_tf_slow)] = _CandleBuffer(candle_tf_slow)

        self._streamer: MarketDataStreamer | None = None
        self._stream_thread: threading.Thread | None = None
        self._reconnect_thread: threading.Thread | None = None
        self._connected = threading.Event()
        self._stop = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._prewarm()
        self._connect_websocket()
        logger.info("DataFeed started — subscribed to %d symbols", len(self._symbols))

    def stop(self) -> None:
        self._stop.set()
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
        df = self.get_candles(symbol, self._tf_fast)
        if df.empty:
            return None
        return float(df.iloc[-1]["close"])

    def get_candles_for_signal(self, symbol: str, tf: int = 5) -> pd.DataFrame:
        """Like get_candles but filters out synthetic candles for signal generation."""
        df = self.get_candles(symbol, tf)
        if df.empty:
            return df
        return df[~df["synthetic"].fillna(False)].reset_index(drop=True)

    # ── Historical pre-warm ───────────────────────────────────────────────────

    def _prewarm(self) -> None:
        today = datetime.now(timezone.utc)
        from_date = (today - timedelta(days=self._prewarm_days)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")
        self._fetch_historical(from_date, to_date)

    def _fetch_historical(self, from_date: str, to_date: str) -> None:
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
                        vol = int(c[5])
                        if vol == 0:
                            continue  # skip zero-volume candles for indicator inputs
                        rows.append({
                            "timestamp": pd.to_datetime(c[0]).to_pydatetime(),
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": vol,
                            "synthetic": False,
                        })
                    self._buffers[(sym, tf)].seed(rows)
                    logger.info("Pre-warmed %s tf=%dmin  %d candles", sym, tf, len(rows))
                except Exception as exc:
                    logger.warning("Pre-warm failed for %s tf=%d: %s", sym, tf, exc)

    # ── Gap fill after reconnect ──────────────────────────────────────────────

    def _gap_fill(self) -> None:
        """Fetch missed candles from REST after a WebSocket disconnect."""
        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")
        gap_count = 0
        for sym in self._symbols:
            for tf in (self._tf_fast, self._tf_slow):
                buf = self._buffers[(sym, tf)]
                last_ts = buf.last_completed_ts()
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
                        ts = pd.to_datetime(c[0]).to_pydatetime()
                        if last_ts and ts <= last_ts:
                            continue  # already have this
                        vol = int(c[5])
                        rows.append({
                            "timestamp": ts,
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": vol,
                            "synthetic": True,  # gap-filled
                        })
                    buf.seed(rows)
                    gap_count += len(rows)
                except Exception as exc:
                    logger.warning("Gap fill failed for %s tf=%d: %s", sym, tf, exc)

        logger.info("Gap fill complete — %d synthetic candles added", gap_count)
        return gap_count

    # ── WebSocket connection + reconnect ─────────────────────────────────────

    def _connect_websocket(self) -> None:
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, daemon=True, name="ws-reconnect"
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                self._connected.clear()
                self._start_streamer()
                self._connected.wait(timeout=30)
                if self._connected.is_set():
                    backoff = 2  # reset on successful connect
                    # Block until disconnect (streamer thread exits)
                    if self._stream_thread:
                        self._stream_thread.join()
                if self._stop.is_set():
                    break
                logger.warning("WebSocket disconnected — attempting gap fill then reconnect in %ds", backoff)
                try:
                    count = self._gap_fill()
                    logger.info("FEED_RECONNECTED gap_filled=%d", count)
                except Exception as exc:
                    logger.warning("Gap fill error: %s", exc)
                time.sleep(backoff)
                backoff = min(backoff * 2, _MAX_RECONNECT_WAIT)
            except Exception as exc:
                logger.error("Reconnect loop error: %s", exc)
                time.sleep(backoff)
                backoff = min(backoff * 2, _MAX_RECONNECT_WAIT)

    def _start_streamer(self) -> None:
        streamer = MarketDataStreamer(
            api_client=self._api_client,
            instrumentKeys=self._symbols,
            mode="full",
        )

        def on_message(message: Any) -> None:
            try:
                feeds = message.get("feeds", {})
                for sym, feed_data in feeds.items():
                    if sym not in self._symbols:
                        continue
                    ff = feed_data.get("ff", {})
                    market_ff = ff.get("marketFF", {})
                    ltpc = market_ff.get("ltpc", {})
                    ohlc_list = market_ff.get("marketOHLC", {}).get("ohlc", [{}])

                    ltp = float(ltpc.get("ltp", 0))
                    volume = int(market_ff.get("tv", 0))
                    high = float(ohlc_list[0].get("high", ltp)) if ohlc_list else ltp
                    low = float(ohlc_list[0].get("low", ltp)) if ohlc_list else ltp

                    # Use exchange timestamp from payload if available
                    raw_ts = ltpc.get("ltt") or ltpc.get("cp")  # last trade time
                    if raw_ts:
                        try:
                            ts = datetime.fromtimestamp(int(raw_ts) / 1000)
                        except Exception:
                            ts = datetime.now()
                    else:
                        ts = datetime.now()

                    for tf in (self._tf_fast, self._tf_slow):
                        buf = self._buffers.get((sym, tf))
                        if buf:
                            buf.on_tick(ltp, volume, high, low, ts)
            except Exception as exc:
                logger.debug("Tick parse error: %s", exc)

        def on_open() -> None:
            self._connected.set()
            logger.info("WebSocket connected and subscribed")

        def on_close() -> None:
            logger.warning("WebSocket closed")

        def on_error(err: Any) -> None:
            logger.error("WebSocket error: %s", err)

        streamer.on("message", on_message)
        streamer.on("open", on_open)
        streamer.on("close", on_close)
        streamer.on("error", on_error)

        self._streamer = streamer
        self._stream_thread = threading.Thread(target=streamer.connect, daemon=True, name="ws-stream")
        self._stream_thread.start()
