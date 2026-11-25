"""Nazorat jadvallari (SQLite):

* `ProductLog` — yangi qo'shilgan mahsulotlar (kim, qachon, kategoriya, birlik, Мин. норматив, birinchi kirim).
  Boss kunlik (20:00) va haftalik (shanba 20:30) hisobotidagi «🆕 Yangi qo'shilgan mahsulotlar» bo'limi uchun.
* `UnpricedStore` — narxi kiritilmagan Приход qatorlari: eslatmalar (1-eslatma, 11:00 qat'iy ogohlantirish),
  12:00 dan keyingi Boss nazorati va hal bo'lgan vaqt. Haqiqiy manba — Приход varag'i: `reconcile()` jadvalni
  varaq bilan moslaydi (qo'lda narx yozilsa ham hal bo'ladi). Kalit — (nom, sana, miqdor): qator raqami
  o'zgarsa ham eslatmalar hisobi yo'qolmaydi."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional, Sequence, Tuple

from app.storage.database import Database


def _k(name: str) -> str:
    return " ".join(str(name).split()).casefold()


@dataclass
class NewProduct:
    created_at: int
    day: str
    user_name: str
    name: str
    category: str
    unit: str
    min_stock: float
    first_qty: Optional[float]
    first_price: Optional[float]


class ProductLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, day: date, user_id: Optional[int], user_name: str, name: str, category: str, unit: str,
                  min_stock: float) -> None:
        await self._db.execute(
            "INSERT INTO new_products (created_at, day, user_id, user_name, name, category, unit, min_stock) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(time.time()), day.isoformat(), user_id, user_name, name, category, unit, min_stock))

    async def note_first_inflow(self, name: str, qty: float, price: Optional[float]) -> None:
        """Yangi mahsulotning BIRINCHI kirimi (keyingilari yozilmaydi)."""
        rows = await self._db.fetchall("SELECT id, name FROM new_products WHERE first_qty IS NULL")
        for r in rows:
            if _k(r["name"]) == _k(name):
                await self._db.execute("UPDATE new_products SET first_qty = ?, first_price = ? WHERE id = ?",
                                       (qty, price, r["id"]))

    async def rename(self, old: str, new: str) -> None:
        rows = await self._db.fetchall("SELECT id, name FROM new_products")
        for r in rows:
            if _k(r["name"]) == _k(old):
                await self._db.execute("UPDATE new_products SET name = ? WHERE id = ?", (new, r["id"]))

    async def between(self, start: date, end: date) -> List[NewProduct]:
        rows = await self._db.fetchall(
            "SELECT * FROM new_products WHERE day >= ? AND day <= ? ORDER BY created_at",
            (start.isoformat(), end.isoformat()))
        return [NewProduct(r["created_at"], r["day"], r["user_name"] or "", r["name"], r["category"] or "",
                           r["unit"] or "", r["min_stock"] or 0.0, r["first_qty"], r["first_price"]) for r in rows]


@dataclass
class UnpricedItem:
    id: int
    sheet_row: Optional[int]
    product_name: str
    qty: float
    unit: str
    entry_date: str
    created_at: int
    first_reminded: Optional[int]
    second_reminded: Optional[int]
    escalated_at: Optional[int]
    reminder_count: int

    def days_open(self, today: date) -> int:
        try:
            return max(0, (today - date.fromisoformat(self.entry_date)).days)
        except ValueError:
            return 0


def _item(r) -> UnpricedItem:
    return UnpricedItem(r["id"], r["sheet_row"], r["product_name"], r["qty"] or 0.0, r["unit"] or "",
                        r["entry_date"], r["created_at"], r["first_reminded"], r["second_reminded"],
                        r["escalated_at"], r["reminder_count"] or 0)


class UnpricedStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _key(name: str, entry_date: str, qty: float) -> Tuple[str, str, float]:
        return _k(name), entry_date, round(float(qty or 0), 4)

    async def open_items(self) -> List[UnpricedItem]:
        rows = await self._db.fetchall("SELECT * FROM unpriced_items WHERE resolved = 0 ORDER BY entry_date, id")
        return [_item(r) for r in rows]

    async def add(self, sheet_row: Optional[int], name: str, qty: float, unit: str, entry_date: str,
                  now: Optional[int] = None) -> None:
        for it in await self.open_items():
            if self._key(it.product_name, it.entry_date, it.qty) == self._key(name, entry_date, qty):
                return
        await self._db.execute(
            "INSERT INTO unpriced_items (sheet_row, product_name, qty, unit, entry_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (sheet_row, name, qty, unit, entry_date, now or int(time.time())))

    async def resolve(self, name: str, entry_date: str, qty: float, now: Optional[int] = None) -> None:
        for it in await self.open_items():
            if self._key(it.product_name, it.entry_date, it.qty) == self._key(name, entry_date, qty):
                await self._db.execute("UPDATE unpriced_items SET resolved = 1, resolved_at = ? WHERE id = ?",
                                       (now or int(time.time()), it.id))
                return

    async def reconcile(self, sheet_rows: Iterable[Tuple[Optional[int], str, float, str, str]],
                        now: Optional[int] = None) -> None:
        """Приход varag'idagi haqiqiy holat: (qator, nom, miqdor, birlik, sana). Varaqda yo'qlari hal bo'lgan,
        varaqda bor, lekin jadvalda yo'qlari qo'shiladi (masalan, qo'lda kiritilgan qator)."""
        sheet = list(sheet_rows)
        keys = {self._key(n, d, q) for _r, n, q, _u, d in sheet}
        for it in await self.open_items():
            if self._key(it.product_name, it.entry_date, it.qty) not in keys:
                await self._db.execute("UPDATE unpriced_items SET resolved = 1, resolved_at = ? WHERE id = ?",
                                       (now or int(time.time()), it.id))
        for row, name, qty, unit, day in sheet:
            await self.add(row, name, qty, unit, day, now)
        by_key = {self._key(n, d, q): r for r, n, q, _u, d in sheet}
        for it in await self.open_items():                       # qator raqami o'zgargan bo'lsa yangilanadi
            row = by_key.get(self._key(it.product_name, it.entry_date, it.qty))
            if row is not None and it.sheet_row != row:
                await self._db.execute("UPDATE unpriced_items SET sheet_row = ? WHERE id = ?", (row, it.id))

    async def mark(self, ids: Sequence[int], field: str, now: Optional[int] = None) -> None:
        """field: first_reminded | second_reminded (eslatmalar soni +1) | escalated_at."""
        if field not in ("first_reminded", "second_reminded", "escalated_at"):
            raise ValueError(field)
        bump = ", reminder_count = reminder_count + 1" if field != "escalated_at" else ""
        for i in ids:
            await self._db.execute(f"UPDATE unpriced_items SET {field} = ?{bump} WHERE id = ?",
                                   (now or int(time.time()), i))
