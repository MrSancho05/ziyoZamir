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

CREATE TABLE IF NOT EXISTS pending_reports (
    kind      TEXT NOT NULL,
    report_key TEXT NOT NULL,
    user_id   INTEGER,
    ready_at  INTEGER NOT NULL,
    sent_at   INTEGER,
    auto      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, report_key)
);

CREATE TABLE IF NOT EXISTS attendance_marks (
    day        TEXT NOT NULL,
    worker_name TEXT NOT NULL,
    status     TEXT NOT NULL,
    marked_by  INTEGER,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (day, worker_name)
);

CREATE TABLE IF NOT EXISTS attendance_sent (
    day     TEXT PRIMARY KEY,
    sent_at INTEGER NOT NULL,
    auto    INTEGER NOT NULL DEFAULT 0,
    sent_by INTEGER
);

CREATE TABLE IF NOT EXISTS attendance_notices (
    day         TEXT NOT NULL,
    worker_name TEXT NOT NULL,
    status      TEXT NOT NULL,
    sent_at     INTEGER NOT NULL,
    PRIMARY KEY (day, worker_name)
);

CREATE TABLE IF NOT EXISTS flow_snapshots (
    sheet     TEXT PRIMARY KEY,
    day       TEXT NOT NULL,
    hash      TEXT NOT NULL,
    rows_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flow_rows (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    day    TEXT NOT NULL,
    sheet  TEXT NOT NULL,
    label  TEXT NOT NULL,
    qty    REAL,
    amount INTEGER
);
CREATE INDEX IF NOT EXISTS idx_flow_rows_day ON flow_rows (sheet, day);

CREATE TABLE IF NOT EXISTS categories (
    name       TEXT PRIMARY KEY,           -- Google Sheets "Категория" ustuniga yoziladigan nom
    sort       INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS new_products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  INTEGER NOT NULL,            -- unix vaqt
    day         TEXT NOT NULL,               -- YYYY-MM-DD (Asia/Tashkent)
    user_id     INTEGER,
    user_name   TEXT,
    name        TEXT NOT NULL,
    category    TEXT,
    unit        TEXT,
    min_stock   REAL,
    first_qty   REAL,                        -- birinchi kirim (bo'lsa)
    first_price REAL
);

CREATE TABLE IF NOT EXISTS unpriced_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_row       INTEGER,
    product_name    TEXT NOT NULL,
    qty             REAL,
    unit            TEXT,
    entry_date      TEXT NOT NULL,           -- Приход dagi sana (YYYY-MM-DD)
    created_at      INTEGER NOT NULL,        -- bot birinchi marta ko'rgan vaqt
    first_reminded  INTEGER,
    second_reminded INTEGER,
    escalated_at    INTEGER,                 -- 12:00 dan keyin Boss nazoratiga o'tgan vaqt
    reminder_count  INTEGER NOT NULL DEFAULT 0,
    resolved        INTEGER NOT NULL DEFAULT 0,
    resolved_at     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_unpriced_open ON unpriced_items (resolved, entry_date);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  INTEGER NOT NULL,            -- unix vaqt
    user_id     INTEGER,
    user_name   TEXT,
    source      TEXT,                        -- ai | menu | bot
    action      TEXT NOT NULL,               -- inflow | del_in | edit_out | shop | del_other ...
    target      TEXT,
    details     TEXT                         -- JSON
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_cache (
    kind       TEXT NOT NULL,              -- w (haftalik) | m (oylik) | fd (kunlik kirim/chiqim)
    report_key TEXT NOT NULL,
    lang       TEXT NOT NULL,
    payload    TEXT NOT NULL,              -- {bo'lim: matn} JSON
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (kind, report_key, lang)
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
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(users)")}
            if "worker_name" not in cols:      # eski bazani yangilash
                self._conn.execute("ALTER TABLE users ADD COLUMN worker_name TEXT")
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
