"""Prixod / Rasxod varaqlarini tahlil qilish. Google Sheets ga bog'liq emas.

Varaq tuzilishi oldindan ma'lum emas, shuning uchun:
- sarlavha qatori o'zi aniqlanadi (matnli kataklari eng ko'p qator);
- mahsulot / miqdor / narx / summa ustunlari sarlavha so'zlari bo'yicha topiladi;
- "bugun nima qo'shildi" varaqning kun boshidagi nusxasi (snapshot) bilan solishtirib aniqlanadi.
"""
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services.attendance_grid import month_from_text
from app.services.payroll import parse_money

_DATE = ("дата", "sana", "date", "сана")
_PRODUCT = ("наименование", "товар", "mahsulot", "nomi", "название", "product", "маҳсулот")
_QTY = ("кол", "miqdor", "soni", "qty")
_SUM = ("сумма", "summa", "jami", "итог", "total", "umumiy")
_PRICE = ("цена", "narx", "price")
_TOTAL_WORDS = ("итог", "jami", "total", "всего", "umumiy")


def norm_rows(values: list[list[Any]]) -> list[tuple[str, ...]]:
    """Bo'sh qatorlar va qator oxiridagi bo'sh kataklar olib tashlanadi."""
    rows: list[tuple[str, ...]] = []
    for r in values:
        cells = [str(c).strip() for c in r]
        while cells and cells[-1] == "":
            cells.pop()
        if cells:
            rows.append(tuple(cells))
    return rows


def sheet_hash(rows: list[tuple[str, ...]]) -> str:
    return hashlib.sha1(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def to_float(value: Any) -> float | None:
    s = re.sub(r"[^\d,.\-]", "", str(value))
    if not re.search(r"\d", s):
        return None
    s = s.replace(",", ".") if ("," in s and "." not in s) else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _is_number(s: str) -> bool:
    return to_float(s) is not None and not re.search(r"[^\d\s,.\-]", s.replace("uzs", ""))


_NUM_DATE = re.compile(r"^(\d{1,2})[./\-](\d{1,2})(?:[./\-](\d{2}|\d{4}))?$")
_ISO_DATE = re.compile(r"^(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})$")
_TEXT_DATE = re.compile(r"^(\d{1,2})\s+([^\s\d]+)\.?(?:\s+(\d{4}))?\s*(?:г\.?|y\.?|yil)?$", re.IGNORECASE)


def parse_cell_date(value: Any, default_year: int) -> date | None:
    """'15.11.2026', '15/11/26', '2026-11-15', '15.11', '15 ноября 2026' -> date. Kun birinchi (dd.mm).
    Sana bo'lmasa (yoki mavjud bo'lmagan sana bo'lsa) None."""
    s = str(value).strip()
    if not s:
        return None
    try:
        m = _ISO_DATE.match(s)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = _NUM_DATE.match(s)
        if m:
            year = int(m[3]) if m[3] else default_year
            if m[3] and len(m[3]) == 2:
                year += 2000
            return date(year, int(m[2]), int(m[1]))
        m = _TEXT_DATE.match(s)
        if m:
            month = month_from_text(m[2])
            if month:
                return date(int(m[3]) if m[3] else default_year, month, int(m[1]))
    except ValueError:
        return None
    return None


def detect_header(rows: list[tuple[str, ...]]) -> int | None:
    """Dastlabki 8 qator ichidan matnli (raqam bo'lmagan) kataklari eng ko'p qator."""
    best, best_score = None, 1
    for i, row in enumerate(rows[:8]):
        score = sum(1 for c in row if c and not _is_number(c))
        if score > best_score:
            best, best_score = i, score
    return best


def find_cols(header: tuple[str, ...]) -> dict[str, int | None]:
    cols: dict[str, int | None] = {"date": None, "product": None, "qty": None, "sum": None, "price": None}
    used: set[int] = set()
    for key, synonyms in (("date", _DATE), ("product", _PRODUCT), ("qty", _QTY), ("sum", _SUM), ("price", _PRICE)):
        for i, h in enumerate(header):
            if i in used:
                continue
            if any(sn in h.lower() for sn in synonyms):
                cols[key] = i
                used.add(i)
                break
    return cols


@dataclass(frozen=True, slots=True)
class FlowRow:
    label: str
    qty: float | None
    price: int | None
    amount: int | None
    text: str          # ustunlari aniqlanmagan holat uchun: "Sarlavha: qiymat | ..."


def _is_total_row(row: tuple[str, ...]) -> bool:
    first = next((c for c in row if c), "")
    return first.lower().startswith(_TOTAL_WORDS)


def row_info(header: tuple[str, ...], cols: dict[str, int | None], row: tuple[str, ...]) -> FlowRow:
    def cell(key: str) -> str:
        i = cols.get(key)
        return row[i] if i is not None and i < len(row) else ""

    qty = to_float(cell("qty")) if cell("qty") else None
    price = parse_money(cell("price")) if cell("price") else None
    total = parse_money(cell("sum")) if cell("sum") else None
    if total is not None:
        amount = total
    elif price is not None and qty is not None:
        amount = int(price * qty + 0.5)
    else:
        amount = price
    label = cell("product") or next(
        (c for c in row if c and not _is_number(c) and parse_cell_date(c, 2000) is None), "")
    text = " | ".join(f"{header[i]}: {v}" for i, v in enumerate(row)
                      if v and i < len(header) and header[i]) or " | ".join(c for c in row if c)
    return FlowRow(label=label, qty=qty, price=price, amount=amount, text=text)


@dataclass(frozen=True, slots=True)
class FlowTable:
    header: tuple[str, ...]
    cols: dict[str, int | None]
    data: list[tuple[str, ...]]      # sarlavhadan keyingi ma'lumot qatorlari ("Итого" qatorlarisiz)


def analyze(rows: list[tuple[str, ...]]) -> FlowTable:
    idx = detect_header(rows)
    if idx is None:
        return FlowTable(header=(), cols={"date": None, "product": None, "qty": None, "sum": None,
                                          "price": None},
                         data=[r for r in rows if not _is_total_row(r)])
    header = rows[idx]
    return FlowTable(header=header, cols=find_cols(header),
                     data=[r for r in rows[idx + 1:] if not _is_total_row(r)])


def diff_new(old: list[tuple[str, ...]], new: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """`new` dagi, `old` da yo'q qatorlar (takrorlanuvchi qatorlar ham hisobga olinadi)."""
    remaining = Counter(old)
    out: list[tuple[str, ...]] = []
    for r in new:
        if remaining[r] > 0:
            remaining[r] -= 1
        else:
            out.append(r)
    return out


# ---------------- to'liq qatorlar va joriy kun ----------------
DETAIL_COLS = ("product", "qty", "sum", "price")


def has_detail_cols(cols: dict[str, int | None]) -> bool:
    """Mahsulot / miqdor / narx / summa ustunlaridan kamida bittasi aniqlanganmi (sana hisobga olinmaydi)."""
    return any(cols.get(k) is not None for k in DETAIL_COLS)


def is_complete_row(header: tuple[str, ...], cols: dict[str, int | None], row: tuple[str, ...]) -> bool:
    """Faqat sanasi bor, mahsulot nomi / miqdori / summasi yo'q qatorlar (bo'sh qatorlar) to'liq emas.

    To'liq qator: mahsulot nomi bor, miqdor (ustun bo'lsa) > 0 va summa (summa yoki narx ustuni bo'lsa) > 0."""
    info = row_info(header, cols, row)
    if not info.label.strip():
        return False
    if cols.get("qty") is not None and not (info.qty is not None and info.qty > 0):
        return False
    if (cols.get("sum") is not None or cols.get("price") is not None) \
            and not (info.amount is not None and info.amount > 0):
        return False
    if not has_detail_cols(cols):
        # ustunlar aniqlanmagan: nomdan tashqari kamida bitta raqam (sana emas) bo'lishi kerak
        return any(_is_number(c) and parse_cell_date(c, 2000) is None for c in row if c)
    return True


def rows_for_day(table: FlowTable, day: date) -> list[tuple[str, ...]]:
    """Sana ustuni bo'yicha FAQAT `day` kunidagi to'liq qatorlar. Boshqa sanalar (o'tgan kunlarda qilingan
    o'zgarishlar ham) hisobga olinmaydi. Sanasi bo'sh qator (birlashtirilgan katak) yuqoridagi sanani oladi."""
    col = table.cols.get("date")
    if col is None:
        return []
    out: list[tuple[str, ...]] = []
    current: date | None = None
    for row in table.data:
        raw = row[col] if col < len(row) else ""
        if raw:
            current = parse_cell_date(raw, day.year)      # o'qib bo'lmasa None: qator hisobga olinmaydi
        if current == day and is_complete_row(table.header, table.cols, row):
            out.append(row)
    return out
