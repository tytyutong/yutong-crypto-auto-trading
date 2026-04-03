import sqlite3
from pathlib import Path

from backend.config import settings
from backend.trading.models import ExecutedOrder


class Storage:
    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            db_path = settings.storage_db_path
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    amount REAL NOT NULL,
                    price REAL NOT NULL,
                    mode TEXT NOT NULL,
                    exchange_order_id TEXT NOT NULL,
                    reason TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def add_order(self, order: ExecutedOrder) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO orders(ts, symbol, side, amount, price, mode, exchange_order_id, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.ts.isoformat(),
                    order.symbol,
                    order.side,
                    order.amount,
                    order.price,
                    order.mode,
                    order.exchange_order_id,
                    order.reason,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def recent_orders(self, limit: int = 50, mode: str | None = None, symbol: str | None = None) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            where = []
            args: list[object] = []
            if mode:
                where.append("mode = ?")
                args.append(mode)
            if symbol:
                where.append("symbol = ?")
                args.append(symbol)
            where_sql = f"WHERE {' AND '.join(where)}" if where else ""
            rows = conn.execute(
                f"SELECT * FROM orders {where_sql} ORDER BY id DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
