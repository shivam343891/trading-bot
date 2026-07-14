"""
Historical data store.

Downloads OHLCV via Upstox REST API and caches to local Parquet files.
All backtest reads come from Parquet only — never hit the API during replay.

Cache layout: data_cache/{symbol_safe}/{tf}min.parquet
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_ROOT = Path("data_cache")
_API_SLEEP = 0.5  # seconds between API calls to respect rate limits


def _safe_name(symbol: str) -> str:
    return symbol.replace("|", "_").replace("/", "_")


def _cache_path(symbol: str, tf: int) -> Path:
    return _CACHE_ROOT / _safe_name(symbol) / f"{tf}min.parquet"


def download(
    symbols: list[str],
    from_date: str,
    to_date: str,
    tf: int,
    api_client,
) -> None:
    """
    Fetch OHLCV for all symbols and cache to Parquet.
    Existing cache is merged (deduped) so partial runs can be resumed.
    """
    import upstox_client
    history_api = upstox_client.HistoryApi(api_client)

    for sym in symbols:
        cache = _cache_path(sym, tf)
        cache.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Downloading %s tf=%dmin from %s to %s", sym, tf, from_date, to_date)
        try:
            resp = history_api.get_historical_candle_data1(
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
                rows.append({
                    "timestamp": pd.to_datetime(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": int(c[5]),
                })

            if not rows:
                logger.warning("No data returned for %s tf=%d", sym, tf)
                time.sleep(_API_SLEEP)
                continue

            new_df = pd.DataFrame(rows)
            new_df["timestamp"] = pd.to_datetime(new_df["timestamp"])

            # Merge with existing cache
            if cache.exists():
                existing = pd.read_parquet(cache)
                combined = pd.concat([existing, new_df], ignore_index=True)
            else:
                combined = new_df

            combined = _clean(combined)
            combined.to_parquet(cache, index=False)
            logger.info("Cached %d candles for %s tf=%d (actual range: %s → %s)",
                        len(combined), sym, tf,
                        combined["timestamp"].min(), combined["timestamp"].max())

        except Exception as exc:
            logger.error("Download failed for %s tf=%d: %s", sym, tf, exc)

        time.sleep(_API_SLEEP)


def load(symbol: str, tf: int, from_date: str | None = None, to_date: str | None = None) -> pd.DataFrame:
    """Load cached Parquet for a symbol/tf, optionally filtered by date range."""
    path = _cache_path(symbol, tf)
    if not path.exists():
        raise FileNotFoundError(
            f"No cached data for {symbol} tf={tf}min at {path}. "
            "Run download() first."
        )

    df = pd.read_parquet(path)
    df = _clean(df)

    if from_date:
        df = df[df["timestamp"] >= pd.Timestamp(from_date)]
    if to_date:
        df = df[df["timestamp"] <= pd.Timestamp(to_date) + pd.Timedelta(days=1)]

    return df.reset_index(drop=True)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and clean a candles DataFrame."""
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # Drop duplicates
    before = len(df)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if len(df) < before:
        logger.debug("Dropped %d duplicate candles", before - len(df))

    # Keep only market hours 09:15–15:30
    t = df["timestamp"].dt.time
    market_start = pd.Timestamp("09:15").time()
    market_end = pd.Timestamp("15:30").time()
    df = df[(t >= market_start) & (t <= market_end)].reset_index(drop=True)

    # Flag gaps (but keep them in the file — engine will handle)
    df = df.sort_values("timestamp").reset_index(drop=True)
    if len(df) > 1:
        gaps = df["timestamp"].diff().dt.total_seconds() / 60
        gap_rows = gaps[gaps > (df["timestamp"].diff().mode()[0].total_seconds() / 60 * 2)]
        if not gap_rows.empty:
            logger.debug("Data gaps detected at: %s", df.loc[gap_rows.index, "timestamp"].tolist()[:5])

    return df
