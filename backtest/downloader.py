"""
Standalone downloader — run once to populate the Parquet cache.

Usage:
    python -m backtest.downloader --from 2025-01-01 --to 2026-07-01
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import config
from backtest import data_store

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _build_api_client():
    import json
    import upstox_client
    from pathlib import Path as P

    token_path = P(config.UPSTOX_TOKEN_FILE)
    cfg = upstox_client.Configuration()
    if token_path.exists():
        token_data = json.loads(token_path.read_text())
        cfg.access_token = token_data.get("access_token", "")
    return upstox_client.ApiClient(configuration=cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and cache historical candles")
    parser.add_argument("--from", dest="from_date", required=True)
    parser.add_argument("--to", dest="to_date", required=True)
    parser.add_argument("--symbols", default=",".join(config.SYMBOLS))
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    api_client = _build_api_client()

    for tf in (5, 15):
        data_store.download(symbols, args.from_date, args.to_date, tf, api_client)

    print("Download complete. Run backtest with:")
    print(f"  python -m backtest --strategies all --from {args.from_date} --to {args.to_date}")


if __name__ == "__main__":
    main()
