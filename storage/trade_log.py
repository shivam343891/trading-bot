"""
SQLite trade journal.

Schema (trades table):
  id            INTEGER PRIMARY KEY
  symbol        TEXT
  side          TEXT          (BUY / SELL)
  qty           INTEGER
  entry_price   REAL
  exit_price    REAL
  sl            REAL
  target        REAL
  entry_time    TEXT          (ISO-8601)
  exit_time     TEXT
  pnl           REAL
  exit_reason   TEXT          (target_hit | sl_hit | eod_flatten | manual)
"""
import csv
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class TradeLog:
    def __init__(self, db_path: str, logs_dir: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(logs_dir).mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._logs_dir = logs_dir
        self._init_db()

    # ── Schema ─────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT    NOT NULL,
                    side        TEXT    NOT NULL,
                    qty         INTEGER NOT NULL,
                    entry_price REAL    NOT NULL,
                    exit_price  REAL,
                    sl          REAL    NOT NULL,
                    target      REAL    NOT NULL,
                    entry_time  TEXT    NOT NULL,
                    exit_time   TEXT,
                    pnl         REAL,
                    exit_reason TEXT
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    # ── Write API ─────────────────────────────────────────────────────────────

    def insert(
        self,
        symbol: str,
        side: str,
        qty: int,
        entry_price: float,
        sl: float,
        target: float,
        entry_time: datetime,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades
                   (symbol, side, qty, entry_price, sl, target, entry_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (symbol, side, qty, entry_price, sl, target, entry_time.isoformat()),
            )
            trade_id = cur.lastrowid
        logger.debug("Trade inserted id=%d  %s %s qty=%d @%.2f", trade_id, side, symbol, qty, entry_price)
        return trade_id

    def update_exit(
        self,
        trade_id: int,
        exit_price: float,
        exit_time: datetime,
        pnl: float,
        exit_reason: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE trades
                   SET exit_price=?, exit_time=?, pnl=?, exit_reason=?
                   WHERE id=?""",
                (exit_price, exit_time.isoformat(), pnl, exit_reason, trade_id),
            )
        logger.debug("Trade updated id=%d  pnl=%.2f  reason=%s", trade_id, pnl, exit_reason)

    # ── Read API ──────────────────────────────────────────────────────────────

    def get_today(self) -> list[dict]:
        today = date.today().isoformat()
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE entry_time LIKE ?", (f"{today}%",)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all(self) -> list[dict]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # ── Export ────────────────────────────────────────────────────────────────

    def export_csv(self, export_date: date | None = None) -> str:
        target_date = export_date or date.today()
        rows = self.get_today() if target_date == date.today() else self._get_by_date(target_date)
        filepath = Path(self._logs_dir) / f"{target_date.isoformat()}.csv"

        if not rows:
            logger.info("No trades to export for %s", target_date)
            return str(filepath)

        fieldnames = ["id", "symbol", "side", "qty", "entry_price", "exit_price",
                      "sl", "target", "entry_time", "exit_time", "pnl", "exit_reason"]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        logger.info("Exported %d trades to %s", len(rows), filepath)
        return str(filepath)

    def _get_by_date(self, d: date) -> list[dict]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE entry_time LIKE ?", (f"{d.isoformat()}%",)
            ).fetchall()
        return [dict(r) for r in rows]
