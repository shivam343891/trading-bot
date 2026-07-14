"""
NSE corporate announcement news filter (Phase 5).

Fetches today's corporate announcements from NSE and flags symbols
that have news, so mean-reversion doesn't fade informed moves.

Fail-closed: on any fetch failure, ALL symbols are flagged as having news.

Also respects NEWS_BLACKOUT_DATES in config (budget day, RBI policy, expiry).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

_NSE_CORP_URL = (
    "https://www.nseindia.com/api/corporate-announcements"
    "?index=equities&from_date={from_date}&to_date={to_date}"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}


class NewsFilter:
    def __init__(self, symbol_to_nse_code: dict[str, str]) -> None:
        """
        symbol_to_nse_code: maps Upstox instrument key → NSE symbol string
        e.g. {"NSE_EQ|INE009A01021": "INFY"}
        """
        self._symbol_map = symbol_to_nse_code
        # Reverse map: NSE code → Upstox key
        self._nse_to_upstox = {v: k for k, v in symbol_to_nse_code.items()}
        self._news_symbols: set[str] = set()  # Upstox instrument keys
        self._fail_closed = False
        self._last_refresh: datetime | None = None

    def refresh(self) -> None:
        """Fetch today's announcements and update internal news set."""
        import config
        today = date.today().isoformat()

        # Blackout dates — always fail closed
        if today in (config.NEWS_BLACKOUT_DATES or []):
            logger.info("NEWS_BLACKOUT_DATE %s — all symbols flagged", today)
            self._news_symbols = set(self._symbol_map.keys())
            self._fail_closed = False
            self._last_refresh = datetime.now()
            return

        try:
            import requests
            # NSE requires a session cookie from the homepage first
            session = requests.Session()
            session.headers.update(_HEADERS)
            session.get("https://www.nseindia.com", timeout=10)

            url = _NSE_CORP_URL.format(from_date=today, to_date=today)
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            data: list[dict[str, Any]] = resp.json()

            flagged: set[str] = set()
            for item in data:
                nse_code = item.get("symbol", "").upper()
                upstox_key = self._nse_to_upstox.get(nse_code)
                if upstox_key:
                    flagged.add(upstox_key)
                    logger.debug("News flagged for %s (%s)", upstox_key, nse_code)

            self._news_symbols = flagged
            self._fail_closed = False
            self._last_refresh = datetime.now()
            logger.info("News filter refreshed — %d symbols have news today", len(flagged))

        except Exception as exc:
            # Fail closed: treat all symbols as having news
            logger.warning("News filter fetch failed (%s) — failing closed (all symbols flagged)", exc)
            self._news_symbols = set(self._symbol_map.keys())
            self._fail_closed = True
            self._last_refresh = datetime.now()

    def has_news(self, symbol: str) -> bool:
        """Return True if symbol has corporate announcements today."""
        return symbol in self._news_symbols

    @property
    def failed_closed(self) -> bool:
        return self._fail_closed
