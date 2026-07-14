"""
NSE trading holiday list.

Update the HOLIDAYS set each year. A warning is logged if the list
doesn't include the current year, so it's hard to forget to update.
"""
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)

# NSE equity segment holidays 2025 and 2026
# Source: NSE India official holiday calendar
HOLIDAYS: frozenset[date] = frozenset([
    # 2025
    date(2025, 1, 26),   # Republic Day
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr (Ramzan Eid)
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti / Mahavir Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Gandhi Jayanti / Dussehra
    date(2025, 10, 24),  # Diwali Laxmi Pujan (Muhurat trading — exchange may open briefly)
    date(2025, 10, 25),  # Diwali Balipratipada
    date(2025, 11, 5),   # Prakash Gurpurb Sri Guru Nanak Dev Ji
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 20),   # Holi
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 10),   # Id-Ul-Fitr
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 8, 17),   # Ganesh Chaturthi (estimated)
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 11, 14),  # Diwali Laxmi Pujan (estimated)
    date(2026, 11, 25),  # Guru Nanak Jayanti (estimated)
    date(2026, 12, 25),  # Christmas
])

_LISTED_YEARS = {d.year for d in HOLIDAYS}


def is_market_holiday(d: date | None = None) -> bool:
    """Return True if `d` is a weekend or NSE holiday."""
    if d is None:
        d = date.today()

    if d.year not in _LISTED_YEARS:
        logger.warning(
            "nse_holidays.py does not contain entries for %d — "
            "update HOLIDAYS before trading in this year.", d.year
        )

    # Saturday=5, Sunday=6
    if d.weekday() >= 5:
        return True

    return d in HOLIDAYS
