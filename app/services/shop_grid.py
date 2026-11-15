"""Do'kon xarajatlari jadvalini tahlil qilish. Google Sheets ga bog'liq emas.

Blok tuzilishi (varaqda oylar ketma-ket joylashadi):
    oy nomi qatori ("Октябрь")
    sarlavha: "День | Цена"
    1..31 kunlar (A - kun raqami, B - summa)
    "Итог:" qatori
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.services.attendance_grid import month_from_text, months_in_range
from app.services.payroll import parse_money

_DAY_HEADERS = ("день", "дни", "kun", "kunlar", "sana")
_TOTAL_PREFIXES = ("итог", "jami", "total", "umumiy", "всего")
_AMOUNT_HEADERS = ("цена", "narx", "сумма", "summa", "xarajat", "расход", "price")


def _norm(v: Any) -> str:
    return str(v).strip().lower().replace("ё", "е")


@dataclass(frozen=True, slots=True)
class ShopBlock:
    month: int | None
    title_row: int           # 1 dan
    title_col: int           # oy nomi yozilgan ustun (0 dan)
    header_row: int          # 1 dan ("День | Цена")
    amount_col: int          # summa ustuni (0 dan)
    day_rows: dict[int, int]  # kun -> qator (1 dan), faqat kun raqami yozilgan qatorlar
    amounts: dict[int, str]   # kun -> katakdagi xom matn ("" = kiritilmagan)
    total_row: int           # "Итог:" qatori (1 dan)

    @property
    def capacity(self) -> int:
        """Sarlavha bilan "Итог" orasidagi qatorlar soni (odatda 31)."""
        return self.total_row - self.header_row - 1


def find_shop_blocks(values: list[list[Any]]) -> list[ShopBlock]:
    blocks: list[ShopBlock] = []
    for i in range(1, len(values)):
        row = values[i]
        if not row or _norm(row[0]) not in _DAY_HEADERS:
            continue

        month, title_col = None, 0
        for c, v in enumerate(values[i - 1][:3]):
            m = month_from_text(v)
            if m:
                month, title_col = m, c
                break
        if month is None:
            continue

        amount_col = 1
        for ci, v in enumerate(row):
            if ci > 0 and _norm(v).startswith(_AMOUNT_HEADERS):
                amount_col = ci
                break

        day_rows: dict[int, int] = {}
        amounts: dict[int, str] = {}
        total_row = None
        for r in range(i + 1, min(i + 46, len(values))):
            cells = values[r]
            a0 = str(cells[0]).strip() if cells else ""
            if a0.isdigit() and 1 <= int(a0) <= 31:
                day_rows[int(a0)] = r + 1
                amounts[int(a0)] = str(cells[amount_col]).strip() if amount_col < len(cells) else ""
            elif _norm(a0).startswith(_TOTAL_PREFIXES):
                total_row = r + 1
                break
            elif a0:
                break        # begona matn: blok tugadi
        if not day_rows:
            continue
        if total_row is None:
            total_row = max(day_rows.values()) + 1
        blocks.append(ShopBlock(month=month, title_row=i, title_col=title_col, header_row=i + 1,
                                amount_col=amount_col, day_rows=day_rows, amounts=amounts,
                                total_row=total_row))
    return blocks


def find_shop_block(values: list[list[Any]], month: int) -> ShopBlock | None:
    return next((b for b in find_shop_blocks(values) if b.month == month), None)


def parse_shop_amount(text: str, signed: bool = False) -> int | None:
    """Foydalanuvchi kiritgan summani songa aylantiradi.

    Qisqa yozuv: 0 dan katta va 1000 dan kichik son avtomatik 1000 ga ko'paytiriladi
    (``450`` -> 450000, ``5`` -> 5000). ``signed=True`` bo'lsa boshidagi ``+``/``-`` belgisi
    qabul qilinadi (``-5`` -> -5000). Noto'g'ri kiritishda None."""
    s = str(text).strip().replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    sign = 1
    if s[:1] in ("+", "-"):
        if not signed:
            return None
        sign = -1 if s[0] == "-" else 1
        s = s[1:].strip()
    if not re.fullmatch(r"[\d\s.,]+", s):
        return None
    amount = parse_money(s)
    if amount is None:
        return None
    if 0 < amount < 1000:
        amount *= 1000
    return sign * amount


def block_amount(block: ShopBlock, day: int) -> int | None:
    """Kun uchun kiritilgan summa. Kiritilmagan yoki o'qib bo'lmasa None."""
    raw = block.amounts.get(day, "")
    return parse_money(raw) if raw else None


@dataclass(slots=True)
class ShopStats:
    total: int
    per_day: dict[date, int]                 # summa kiritilgan kunlar
    missing: list[date]                      # blok bor, lekin summa kiritilmagan kunlar (yakshanbasiz)
    months_missing: list[tuple[int, int]]    # varag'i/bloki topilmagan (yil, oy) lar


def compute_shop_stats(blocks: dict[tuple[int, int], ShopBlock | None], start: date, end: date) -> ShopStats:
    per_day: dict[date, int] = {}
    missing: list[date] = []
    months_missing = [ym for ym in months_in_range(start, end) if blocks.get(ym) is None]
    d = start
    while d <= end:
        block = blocks.get((d.year, d.month))
        if block is not None and d.weekday() != 6 and d.day in block.day_rows:
            amount = block_amount(block, d.day)
            if amount is None:
                missing.append(d)
            else:
                per_day[d] = amount
        d += timedelta(days=1)
    return ShopStats(total=sum(per_day.values()), per_day=per_day, missing=missing,
                     months_missing=months_missing)
