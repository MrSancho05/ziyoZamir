"""Oxirgi kiritilgan minimal normativlar (Мин. остаток) — SQLite (kv jadvali).

Uch darajada eslab qolinadi: o'lchov birligi bo'yicha (шт / кг / каробка ...), kategoriya bo'yicha va umumiy oxirgisi.
Keyingi safar mahsulot qo'shilganda yoki tahrirlanganda eng mos (birlik -> kategoriya -> umumiy) qiymat
"💡 Tavsiya" tugmasi bo'lib chiqadi."""
from __future__ import annotations

from typing import Optional

from app.storage.database import Database

_LAST = "norm:last"


def _unit_key(unit: str) -> str:
    return f"norm:unit:{str(unit).strip().casefold()}"


def _cat_key(category: str) -> str:
    return f"norm:cat:{str(category).strip().casefold()}"


class NormMemory:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def _get(self, key: str) -> Optional[float]:
        row = await self._db.fetchone("SELECT value FROM kv WHERE key = ?", (key,))
        if row is None:
            return None
        try:
            return float(row["value"])
        except (TypeError, ValueError):
            return None

    async def _set(self, key: str, value: float) -> None:
        await self._db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, str(value)))

    async def remember(self, value: float, unit: str = "", category: str = "") -> None:
        """Faqat musbat qiymat eslab qolinadi (0 — "kuzatilmasin" degani, tavsiya sifatida foydasiz)."""
        if value is None or value <= 0:
            return
        await self._set(_LAST, value)
        if unit:
            await self._set(_unit_key(unit), value)
        if category:
            await self._set(_cat_key(category), value)

    async def suggest(self, unit: str = "", category: str = "") -> Optional[float]:
        for key in ([_unit_key(unit)] if unit else []) + ([_cat_key(category)] if category else []) + [_LAST]:
            value = await self._get(key)
            if value:
                return value
        return None
