"""🎞 Кромка ПВХ: o'lcham (1/19, 1/35, 2/19, 2/35) va rang bo'yicha nomlash.

Nom formati: «Кромка {rang} {o'lcham}», masalan «Кромка Noch koma 1/19». Eski yozuvlar ham tushuniladi:
«Кромка ПВХ 1×19», «Kromka Astana 19/1», «кромка 2x35».
Faqat BIRINCHI so'z hisobga olinadi: «Клей для кромки» — bu kley, kromka emas."""
from __future__ import annotations

import re
from typing import Optional

CATEGORY = "Кромка ПВХ"
UNIT = "рулон"
SIZES = ("1/19", "1/35", "2/19", "2/35")

_SEP = r"\s*[/xх×*\\]\s*"
_THICK_FIRST = re.compile(r"(?<!\d)([12])" + _SEP + r"(19|35)(?!\d)", re.IGNORECASE)
_WIDTH_FIRST = re.compile(r"(?<!\d)(19|35)" + _SEP + r"([12])(?!\d)", re.IGNORECASE)
_WORD = re.compile(r"^\s*(кромк\w*|kromk\w*|kromka)\b\.?", re.IGNORECASE)


def is_kromka(text: str) -> bool:
    """Matn (mahsulot nomi yoki qidiruv) kromka haqidami: birinchi so'zi «кромка» / «kromka»."""
    return bool(_WORD.match(str(text or "")))


def parse_size(text: str) -> Optional[str]:
    """'1×19', '19/1', '1x19', '2 / 35' -> '1/19' ... Topilmasa None."""
    s = str(text or "")
    m = _THICK_FIRST.search(s)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = _WIDTH_FIRST.search(s)
    if m:
        return f"{m.group(2)}/{m.group(1)}"
    return None


def color_of(name: str) -> str:
    """«Кромка Noch koma 1/19» -> «Noch koma»; «Кромка ПВХ 1×19» -> «ПВХ»."""
    s = _WORD.sub("", str(name or ""), count=1)
    s = _THICK_FIRST.sub(" ", s)
    s = _WIDTH_FIRST.sub(" ", s)
    return " ".join(s.split()).strip(" -–—,.")


def clean_color(text: str) -> str:
    """Foydalanuvchi yozgan rang: «kromka», o'lcham va ortiqcha belgilar olib tashlanadi."""
    return color_of(text)[:40]


def build_name(color: str, size: str) -> str:
    return f"Кромка {color} {size}"


def matches_size(name: str, size: str) -> bool:
    return is_kromka(name) and parse_size(name) == size
