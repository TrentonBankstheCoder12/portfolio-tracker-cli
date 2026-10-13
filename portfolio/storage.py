"""SQLite persistence for the trade log, using only the standard library.

Decimals are stored as TEXT. SQLite's REAL type is a float and would reintroduce the
rounding errors we use Decimal to avoid.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from .fifo import replay
from .models import Trade, TradeKind

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('BUY', 'SELL', 'DIVIDEND')),
    symbol     TEXT NOT NULL,
    quantity   TEXT NOT NULL,
    price      TEXT NOT NULL,
    fees       TEXT NOT NULL DEFAULT '0'
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_date ON trades (symbol, trade_date);
"""


class TradeStore:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> TradeStore:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def all_trades(self) -> list[Trade]:
        rows = self.conn.execute(
            "SELECT id, trade_date, kind, symbol, quantity, price, fees FROM trades ORDER BY trade_date, id"
        ).fetchall()
        return [
            Trade(
                id=r[0],
                trade_date=date.fromisoformat(r[1]),
                kind=TradeKind(r[2]),
                symbol=r[3],
                quantity=Decimal(r[4]),
                price=Decimal(r[5]),
                fees=Decimal(r[6]),
            )
            for r in rows
        ]

    def add_trades(self, new_trades: list[Trade]) -> list[Trade]:
        """Insert trades atomically, but only if the resulting history is still valid.

        We replay existing + new trades *before* committing. That way a sell that would
        oversell (even a back-dated one that breaks a later sell) is rejected and nothing
        is written. `with self.conn` rolls back automatically if anything raises.
        """
        existing = self.all_trades()
        next_id = max((t.id for t in existing), default=0) + 1
        # Give candidates provisional ids so same-day ordering in the check matches storage.
        candidates = [
            Trade(t.trade_date, t.kind, t.symbol, t.quantity, t.price, t.fees, id=next_id + i)
            for i, t in enumerate(new_trades)
        ]
        replay(existing + candidates)  # raises InsufficientSharesError on oversell

        saved: list[Trade] = []
        with self.conn:
            for t in new_trades:
                cur = self.conn.execute(
                    "INSERT INTO trades (trade_date, kind, symbol, quantity, price, fees) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        t.trade_date.isoformat(),
                        t.kind.value,
                        t.symbol,
                        str(t.quantity),
                        str(t.price),
                        str(t.fees),
                    ),
                )
                saved.append(
                    Trade(t.trade_date, t.kind, t.symbol, t.quantity, t.price, t.fees, id=cur.lastrowid)
                )
        return saved

    def delete_trade(self, trade_id: int) -> bool:
        """Delete a trade unless doing so would leave a later sell without shares."""
        remaining = [t for t in self.all_trades() if t.id != trade_id]
        replay(remaining)
        with self.conn:
            cur = self.conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        return cur.rowcount == 1
