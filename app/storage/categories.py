"""Umumiy (butun katalog uchun bitta) kategoriyalar ro'yxati: SQLite da saqlanadi.

Birinchi ishga tushishda 6 ta standart zavod kategoriyasi qo'shiladi. Jadvalga (Google Sheets "Категория"
ustuniga) doim shu yerdagi `name` yoziladi: standart kategoriyalar uchun bu ruscha kanonik nom, foydalanuvchi
qo'shgan kategoriya uchun aynan saqlangan nom."""
from __future__ import annotations

from typing import List

from app.services.categories import DEFAULT_CATEGORIES
from app.storage.database import Database

_SEED_KEY = "categories_seeded"


class CategoryStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def ensure_seeded(self) -> None:
        if await self._db.fetchone("SELECT value FROM kv WHERE key = ?", (_SEED_KEY,)):
            return
        for i, cat in enumerate(DEFAULT_CATEGORIES):
            await self._db.execute("INSERT OR IGNORE INTO categories (name, sort) VALUES (?, ?)", (cat.name, i))
        await self._db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, '1')", (_SEED_KEY,))

    async def names(self) -> List[str]:
        await self.ensure_seeded()
        rows = await self._db.fetchall("SELECT name FROM categories ORDER BY sort, name COLLATE NOCASE")
        return [r["name"] for r in rows]

    async def add(self, name: str) -> bool:
        """False: shunday nom (katta-kichik harfidan qat'i nazar) allaqachon bor."""
        if any(n.casefold() == name.casefold() for n in await self.names()):
            return False
        await self._db.execute("INSERT INTO categories (name, sort) VALUES (?, 100)", (name,))
        return True

    async def rename(self, old: str, new: str) -> None:
        row = await self._db.fetchone("SELECT sort FROM categories WHERE name = ?", (old,))
        await self._db.execute("DELETE FROM categories WHERE name = ?", (old,))
        await self._db.execute("INSERT OR REPLACE INTO categories (name, sort) VALUES (?, ?)",
                               (new, row["sort"] if row else 100))

    async def delete(self, name: str) -> None:
        await self._db.execute("DELETE FROM categories WHERE name = ?", (name,))
