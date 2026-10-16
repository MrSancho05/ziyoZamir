import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import Any, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    full_name  TEXT NOT NULL,
    username   TEXT,
    role       TEXT,
    lang       TEXT NOT NULL DEFAULT 'uz',
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS report_log (
    kind       TEXT NOT NULL,
    period_key TEXT NOT NULL,
    sent_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (kind, period_key)
);
"""


class Database:
    """SQLite ulanishi. Chaqiruvlar alohida oqimda bajariladi, bot qotib qolmaydi."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _run(self, sql: str, params: Sequence[Any], mode: str) -> Any:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            if mode == "one":
                return cur.fetchone()
            if mode == "all":
                return cur.fetchall()
            return None

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        await asyncio.to_thread(self._run, sql, params, "none")

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return await asyncio.to_thread(self._run, sql, params, "one")

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._run, sql, params, "all")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
